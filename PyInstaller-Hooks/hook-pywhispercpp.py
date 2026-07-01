"""PyInstaller hook for pywhispercpp (whisper.cpp Vulkan ASR backend).

pywhispercpp wraps whisper.cpp/ggml through a compiled ``_pywhispercpp`` C
extension that sits at the site-packages ROOT (not inside the package dir), with
its ggml/whisper shared libs auditwheel/delvewheel-vendored into a sibling
``pywhispercpp.libs`` dir (Linux) / next to the extension or in a delvewheel libs
dir (Windows).  ``collect_all`` / ``collect_dynamic_libs`` only scan INSIDE the
package directory, so for this package both return ZERO binaries (measured on the
installed wheel: ``collect_all`` -> datas=13 binaries=0 hiddenimports=10).  The
extension and its vendored libs are therefore collected EXPLICITLY here, from the
measured on-disk locations, rather than trusting collect_all for binaries.

PyInstaller's import graph DOES reach ``_pywhispercpp`` (model.py/constants.py do
``import _pywhispercpp``) and follows its NEEDED entries to pull the directly
linked ggml/whisper libs (libwhisper, libggml, libggml-base) that auditwheel/
delvewheel vendored next to the extension.  But ``libggml-vulkan`` is a
``GGML_BACKEND_DL`` *module*, ``dlopen``-ed at runtime via ``ggml_backend_load_all``
— it is NOT a NEEDED entry of ``_pywhispercpp``/libggml/libwhisper, so static
analysis never reaches it and it would be left out of the bundle.  It is collected
EXPLICITLY here, from wherever the per-OS wheel-repair tool leaves it: on Linux at
the site-packages ROOT (auditwheel ``--exclude`` keeps the injected module
top-level — step 2c grafts it into ``pywhispercpp.libs``); on Windows as a root
``ggml-vulkan.dll`` (delvewheel — step 2b sweeps it next to the extension).

The shared libs carried into the frozen tree:

  - libwhisper           (the whisper.cpp core)
  - libggml              (ggml dispatcher)
  - libggml-base         (ggml base ops; the CPU kernels are compiled in HERE under
                          GGML_BACKEND_DL, so the current Linux/Windows Vulkan wheels
                          ship no separate libggml-cpu / ggml-cpu.dll module)
  - libggml-vulkan       (Vulkan backend — present ONLY in the Vulkan wheel; the
                          GGML_BACKEND_DL module the ggml registry dlopen-s at
                          runtime, skipped gracefully when the loader is absent)

Placement.  ggml discovers its backends (``ggml_backend_load_all`` / ``_best``) by
scanning the directory the loaded ggml libs live in, so ggml-vulkan must land in
the SAME dir as libggml in the frozen tree.  PyInstaller preserves the
site-packages parent directory of collected binaries (see
``bindepend._get_paths_for_parent_directory_preservation``): the NEEDED-followed
libs from ``site-packages/pywhispercpp.libs`` are placed under
``_internal/pywhispercpp.libs/``.  So we collect the ``.libs`` dir to dest
``pywhispercpp.libs`` (matching that adjacency) and the root extension to dest
``.`` (where PyInstaller's import graph would also place it).  The _engine seam
(anki_miner/services/asr/_engine.py) globs the same set of dirs — package dir,
site root, ``*.libs`` siblings — to find ggml-vulkan, and this collection
reproduces that adjacency in the frozen bundle.

EXCLUDE the Vulkan loader (libvulkan.so.1 / vulkan-1.dll): it must NOT be frozen
into the bundle.  The wheel build already strips it (auditwheel/delvewheel
``--exclude``), so it should not be present to collect — but a defensive filter
here guarantees that even if a future wheel re-vendors it, the system/driver
loader (the user's actual GPU driver) wins at runtime instead of a stale copy.

Harmless when pywhispercpp is absent: ``find_spec`` returns None (the Intel-mac /
no-[asr] builds), so the explicit collection short-circuits to empty lists and
this hook is a no-op there.  (The pywhispercpp/ggml libs are stripped from the
lean .deb stage tree separately; see release.yml.)
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# collect_all still gives us datas (package data, e.g. model.pyi / py.typed) and
# hiddenimports (the function-locally imported submodules).  Its binaries list is
# empty for this package (the extension + libs live OUTSIDE the package dir), so
# we discard it and source the binaries from the measured locations below.
datas, _binaries_unused, hiddenimports = collect_all("pywhispercpp")

binaries: list[tuple[str, str]] = []

try:
    import importlib.util

    _spec = importlib.util.find_spec("pywhispercpp")
except Exception:  # noqa: BLE001 — a broken/absent install means "nothing to collect"
    _spec = None

if _spec is not None and _spec.origin is not None:
    # spec.origin -> .../site-packages/pywhispercpp/__init__.py
    _pkg_dir = Path(_spec.origin).parent
    _site_root = _pkg_dir.parent

    # (1) The compiled extension at the site-packages ROOT (_pywhispercpp.*.so /
    #     _pywhispercpp*.pyd).  Collected to the top-level dir, matching where
    #     PyInstaller's import graph would also place it.
    for _ext in _site_root.glob("_pywhispercpp*"):
        if _ext.is_file():
            binaries.append((str(_ext), "."))

    # (2) The auditwheel/delvewheel-vendored ggml/whisper libs.  auditwheel drops
    #     them in a sibling ``pywhispercpp.libs`` dir (Linux); delvewheel uses a
    #     ``pywhispercpp.libs`` dir too, or places DLLs next to the extension at
    #     the site root (Windows).  Collect both forms, preserving the
    #     ``pywhispercpp.libs`` adjacency so ggml-vulkan sits next to libggml for
    #     runtime ggml_backend_load_all discovery.
    _libs_dir = _site_root / "pywhispercpp.libs"
    if _libs_dir.is_dir():
        for _lib in _libs_dir.iterdir():
            if _lib.is_file():
                binaries.append((str(_lib), "pywhispercpp.libs"))

    # (2b) Windows delvewheel can also leave the vendored DLLs directly beside the
    #      extension at the site root.  Sweep those into the top-level dir (next to
    #      the extension, which is also where bindepend would place root-level
    #      NEEDED-followed libs).
    for _dll in _site_root.glob("ggml*.dll"):
        if _dll.is_file():
            binaries.append((str(_dll), "."))
    for _dll in _site_root.glob("whisper*.dll"):
        if _dll.is_file():
            binaries.append((str(_dll), "."))

    # (2c) Linux: the from-source Vulkan wheel drops the ggml-vulkan MODULE at the
    #      site ROOT, not inside pywhispercpp.libs.  release.yml injects it into the
    #      raw wheel pre-auditwheel and passes ``auditwheel repair --exclude
    #      libggml-vulkan.so``, so auditwheel keeps the file top-level — but still
    #      repairs it in place: its RUNPATH is repointed to
    #      ``$ORIGIN/pywhispercpp.libs`` and its NEEDED to the hashed .libs sonames.
    #      There is no ``.so`` counterpart to the Windows (2b) DLL sweep, so without
    #      this step libggml-vulkan is never collected and the frozen bundle silently
    #      ships a CPU-only tree.  Collect it INTO ``pywhispercpp.libs`` (NOT the site
    #      root ``.``): that is the dir the LOADED libggml lives in (per the extension
    #      RUNPATH), so ggml_backend_load_all discovers it adjacent to libggml at
    #      runtime, its ``$ORIGIN``-relative NEEDED resolve against the sibling hashed
    #      libs, and _engine._find_ggml_vulkan_lib()'s ``*.libs`` search finds it in
    #      the frozen tree.  Dest ``.`` would pass the smoke but leave the module one
    #      dir above where ggml scans — a silent no-Vulkan-on-real-GPU bug.  The guard
    #      is a no-op if a future auditwheel ever vendors it into .libs itself (step 2
    #      already collected it), preventing a duplicate same-dest collision.
    _already_in_libs = {os.path.basename(s) for (s, d) in binaries if d == "pywhispercpp.libs"}
    for _so in _site_root.glob("libggml-vulkan*.so*"):
        if _so.is_file() and _so.name not in _already_in_libs:
            binaries.append((str(_so), "pywhispercpp.libs"))

# Defensive: drop the Vulkan loader if a wheel ever vendors it. The loader is the
# ICD-dispatching shim that must come from the system / GPU driver at runtime, so
# a frozen copy would shadow the driver's. Matched case-insensitively by basename
# (libvulkan.so / libvulkan.so.1 on Linux, vulkan-1.dll on Windows).
_VULKAN_LOADER_NAMES = ("libvulkan.so", "vulkan-1.dll")


def _is_vulkan_loader(dest: str) -> bool:
    base = os.path.basename(dest).lower()
    return any(base == n or base.startswith(n + ".") for n in _VULKAN_LOADER_NAMES)


binaries = [(src, dest) for (src, dest) in binaries if not _is_vulkan_loader(os.path.basename(src))]
