"""Import seam for faster-whisper.

This module is the ONLY place in the codebase that touches faster_whisper
names. All other ASR code imports through these three functions so that:

1. Default CI (no ``[asr]`` extra) stays green — no ImportError at module load.
2. Unit tests can monkeypatch ``available``, ``get_whisper_model_cls``, and
   ``get_download_fn`` without importing the real library.

Never add ``import faster_whisper`` at module top level.

The whisper.cpp (pywhispercpp) seam below mirrors the same discipline: no
top-level ``import pywhispercpp`` or ``import ctypes``; ``whisper_cpp_available``
and ``vulkan_device_count`` never raise; and the Vulkan device count is probed
in a *subprocess* (a broken Vulkan driver can C-abort uncatchably — isolating it
in a child means the abort kills only the child and the parent reads a clean 0).
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def available() -> bool:
    """Return True iff faster-whisper AND its native backend are importable.

    Uses ``importlib.util.find_spec`` so no actual import occurs (and no
    initialisation side-effects). Both ``faster_whisper`` and ``ctranslate2``
    must be findable; missing either returns False.
    """
    return (
        importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec("ctranslate2") is not None
    )


def get_whisper_model_cls():
    """Return ``faster_whisper.WhisperModel`` (function-local import).

    Raises:
        ImportError: If faster_whisper is not installed.
    """
    import faster_whisper  # noqa: PLC0415  (intentional function-local import)

    return faster_whisper.WhisperModel


def get_download_fn():
    """Return ``faster_whisper.download_model`` (function-local import).

    Raises:
        ImportError: If faster_whisper is not installed.
    """
    import faster_whisper  # noqa: PLC0415  (intentional function-local import)

    return faster_whisper.download_model


def cuda_device_count() -> int:
    """Return the number of usable CUDA devices, or 0 on ANY failure.

    Function-local ``ctranslate2`` import (the same no-top-level-import rule as
    the rest of this seam) so default CI without the ``[asr]`` extra stays green.
    Degrades to 0 on anything — ImportError (extra not installed), OSError (a
    broken native CUDA runtime), or any other surprise — so callers can treat a
    nonzero return as "a GPU is present and usable" without their own guard.
    """
    try:
        import ctranslate2  # noqa: PLC0415  (intentional function-local import)

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 — any failure means "no usable GPU"
        return 0


# ---------------------------------------------------------------------------
# whisper.cpp (pywhispercpp) seam — Vulkan-accelerated transcription backend
# ---------------------------------------------------------------------------

# Glob patterns for the ggml-vulkan shared library across platforms. Only the
# Vulkan ggml backend is matched — the CPU wheel ships libggml/libggml-cpu but
# never libggml-vulkan, so a hit here means a GPU-capable build is installed.
_GGML_VULKAN_GLOBS = (
    "libggml-vulkan*.so*",  # Linux
    "ggml-vulkan*.dll",  # Windows
    "libggml-vulkan*.dylib",  # macOS
)

# Glob for the ggml dispatcher lib that exports ggml_backend_load_all[_from_path].
# NOT libggml-base / -cpu / -vulkan (those are backend MODULES); the plain
# dispatcher is what registers them into ggml's backend registry.
_GGML_CORE_GLOBS = (
    # Linux. MUST be "libggml*.so*", not "libggml.so*": auditwheel renames the
    # dispatcher to a hashed soname (e.g. libggml-9964a741.so.0.9.8) in the shipped
    # wheel, and "libggml.so*" would NOT match that (the '-' after libggml breaks it),
    # leaving the registry unloaded -> GGML_ASSERT(device) abort in the bundle. The
    # -base/-cpu/-vulkan backend modules this also matches are removed by the exclude
    # filter in _find_ggml_core_lib (a hex hash never starts with base/cpu/vulkan).
    "libggml*.so*",
    "ggml*.dll",  # Windows (ggml.dll not hashed by delvewheel)
    "libggml*.dylib",  # macOS (not shipped, kept for symmetry)
)

# Guard: ggml_backend_load_all() is called at most once per process. The ggml
# registry is process-global; a second call is a redundant rescan.
_GGML_BACKENDS_LOADED = False


def _ggml_lib_search_dirs() -> list[Path]:
    """Dirs that may hold the ggml DL backend modules (cpu + vulkan).

    Same set :func:`_find_ggml_vulkan_lib` scans: the pywhispercpp package dir, the
    site-packages / frozen root (``spec.origin.parent.parent``), and any sibling
    ``*.libs`` auditwheel dir (in the frozen bundle: ``_internal/pywhispercpp.libs``).
    Returns ``[]`` when pywhispercpp is absent or introspection fails. Never raises.
    """
    try:
        spec = importlib.util.find_spec("pywhispercpp")
        if spec is None or spec.origin is None:
            return []
        pkg_dir = Path(spec.origin).parent
        site_root = pkg_dir.parent
        dirs: list[Path] = [pkg_dir, site_root]
        for sibling in site_root.glob("*.libs"):
            if sibling.is_dir():
                dirs.append(sibling)
        return dirs
    except Exception:  # noqa: BLE001 — a missing/odd install means "no dirs"
        return []


def _find_ggml_vulkan_lib() -> Path | None:
    """Locate the bundled ``ggml-vulkan`` shared lib, or None when absent.

    pywhispercpp's wheels bundle their ggml backends in a few places: inside the
    package dir, directly in site-packages alongside the compiled extension, and
    in a sibling ``pywhispercpp.libs`` / ``*.libs`` auditwheel dir. We search all
    of them (via :func:`_ggml_lib_search_dirs`). Returns None for the dev/CPU wheel
    (which has no ggml-vulkan) so :func:`whisper_cpp_available` reports unavailable.
    Never raises — any introspection failure degrades to None.
    """
    try:
        dirs = _ggml_lib_search_dirs()
        if not dirs:
            return None
        pkg_dir = dirs[0]
        for directory in dirs:
            for pattern in _GGML_VULKAN_GLOBS:
                # The package dir is searched recursively (some wheels nest libs
                # under it); the flat dirs use a shallow glob.
                globber = directory.rglob if directory == pkg_dir else directory.glob
                for hit in globber(pattern):
                    if hit.is_file():
                        return hit
        return None
    except Exception:  # noqa: BLE001 — a missing/odd install means "no Vulkan lib"
        return None


def _find_ggml_core_lib(search_dirs: list[Path]) -> Path | None:
    """Locate libggml (the dispatcher that exports ggml_backend_load_all*).

    Prefer the plain 'libggml' dispatcher, NOT libggml-base / libggml-cpu /
    libggml-vulkan (those are backends). auditwheel renames it to a hashed
    soname (e.g. ``libggml-<hash>.so``) but it still starts with 'libggml' and is
    NOT one of the -base/-cpu/-vulkan modules. Returns None when absent.
    """
    exclude = (
        "libggml-base",
        "libggml-cpu",
        "libggml-vulkan",
        "ggml-base",
        "ggml-cpu",
        "ggml-vulkan",
    )
    for directory in search_dirs:
        for pattern in _GGML_CORE_GLOBS:
            for hit in directory.glob(pattern):
                if hit.is_file() and not any(hit.name.startswith(x) for x in exclude):
                    return hit
    return None


def ensure_ggml_backends_loaded() -> None:
    """Register the ggml DL backend modules (cpu + vulkan) into ggml's registry.

    The from-source Vulkan wheel is ``GGML_BACKEND_DL=1``: libggml-cpu / libggml-vulkan
    are dlopen MODULES that only enter the backend registry via
    ``ggml_backend_load_all()``. pywhispercpp v1.5.0 never calls it, so the registry is
    empty and ``whisper_backend_init_gpu`` asserts (SIGABRT) on the FIRST ``Model()``.
    This calls ``ggml_backend_load_all_from_path(<dir holding the modules>)`` exactly
    once, BEFORE any Model construction.

    No-op and never raises when: pywhispercpp/ggml-vulkan is absent (dev/CPU wheel),
    libggml can't be located, or the symbol is missing (a non-DL prebuilt wheel that
    already self-registers). Idempotent via a module guard (ggml's registry is
    process-global) — the guard is set on EVERY exit path, including exceptions, so a
    load failure never thrashes on each subsequent Model construction.
    """
    global _GGML_BACKENDS_LOADED
    if _GGML_BACKENDS_LOADED:
        return
    try:
        vulkan_lib = _find_ggml_vulkan_lib()
        if vulkan_lib is None:
            _GGML_BACKENDS_LOADED = True  # nothing to load; don't retry every Model
            return
        backend_dir = vulkan_lib.parent
        dirs = _ggml_lib_search_dirs()
        core = _find_ggml_core_lib(dirs)
        if core is None:
            _GGML_BACKENDS_LOADED = True
            return

        import ctypes  # noqa: PLC0415  (module stays importable without pywhispercpp)

        # RTLD_GLOBAL so the loaded backend modules resolve libggml/libggml-base
        # symbols against this same handle (matches how whisper.cpp loads them).
        mode = getattr(ctypes, "RTLD_GLOBAL", 0)
        lib = ctypes.CDLL(str(core), mode=mode) if hasattr(ctypes, "RTLD_GLOBAL") else ctypes.CDLL(str(core))

        fn = getattr(lib, "ggml_backend_load_all_from_path", None)
        if fn is not None:
            fn.restype = None
            fn.argtypes = [ctypes.c_char_p]
            fn(str(backend_dir).encode("utf-8"))
        else:
            # Fallback: older ggml with only the no-arg form (scans exe-dir + cwd).
            fn0 = getattr(lib, "ggml_backend_load_all", None)
            if fn0 is not None:
                fn0.restype = None
                fn0.argtypes = []
                fn0()
        _GGML_BACKENDS_LOADED = True
    except Exception:  # noqa: BLE001 — a load failure must degrade to CPU/CT2, never abort
        _GGML_BACKENDS_LOADED = True  # do not thrash on every Model construction


def whisper_cpp_available() -> bool:
    """Return True iff pywhispercpp is installed AND a ggml-vulkan lib is present.

    Pure check, no heavy import (``importlib.util.find_spec`` only) and never
    raises. The CPU-only wheel ships ggml/ggml-cpu but no ggml-vulkan, so this
    returns False there — the intended "no GPU backend" result.
    """
    try:
        if importlib.util.find_spec("pywhispercpp") is None:
            return False
        return _find_ggml_vulkan_lib() is not None
    except Exception:  # noqa: BLE001 — any failure means "not available"
        return False


def get_whisper_cpp_model_cls():
    """Return ``pywhispercpp.model.Model`` (function-local import).

    Raises:
        ImportError: If pywhispercpp is not installed.
    """
    import pywhispercpp.model  # noqa: PLC0415  (intentional function-local import)

    return pywhispercpp.model.Model


# Per-process memoization for vulkan_device_count: the subprocess probe is
# computed once and cached. None means "not yet computed".
_VULKAN_DEVICE_COUNT: int | None = None


def vulkan_device_count() -> int:
    """Return the number of Vulkan devices ggml sees, or 0 on ANY failure.

    Crash-safe and memoized per process. The count is probed in a *subprocess*
    (`anki_miner.services.asr._vulkan_probe`) because a broken Vulkan driver can
    C-abort uncatchably — running it in a child means such an abort kills only
    the child and we read a clean 0 here. Degrades to 0 on a nonzero exit, a
    timeout, a spawn failure, or unparseable stdout. Never raises.
    """
    global _VULKAN_DEVICE_COUNT
    if _VULKAN_DEVICE_COUNT is not None:
        return _VULKAN_DEVICE_COUNT

    _VULKAN_DEVICE_COUNT = _probe_vulkan_device_count()
    return _VULKAN_DEVICE_COUNT


def _probe_vulkan_device_count() -> int:
    """Run the subprocess probe once and parse its integer stdout (0 on failure)."""
    try:
        if getattr(sys, "frozen", False):
            # A frozen bundle re-invokes itself; app.main() routes the env var
            # into the probe before any Qt init.
            argv = [sys.executable]
            env = {**os.environ, "ANKI_MINER_ASR_VULKAN_PROBE": "1"}
        else:
            argv = [sys.executable, "-m", "anki_miner.services.asr._vulkan_probe"]
            env = None
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if proc.returncode != 0:
            return 0
        return int(proc.stdout.strip())
    except Exception:  # noqa: BLE001 — timeout / spawn / parse failure all mean 0
        return 0
