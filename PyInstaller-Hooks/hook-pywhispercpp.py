"""PyInstaller hook for pywhispercpp (whisper.cpp Vulkan ASR backend).

pywhispercpp wraps whisper.cpp/ggml through a compiled ``_pywhispercpp`` C
extension that sits at the site-packages ROOT (not inside the package dir), with
its ggml/whisper shared libs auditwheel/delvewheel-vendored into a sibling
``pywhispercpp.libs`` dir (Linux) / next to the extension (Windows).  None of
that is reachable by PyInstaller's static import analysis, so we sweep it with
``collect_all`` exactly like hook-faster_whisper.py does for ctranslate2/av.

The Vulkan-enabled wheel (built from source in release.yml with GGML_VULKAN=1
GGML_BACKEND_DL=1) ships these shared libs, which collect_all picks up as
binaries:

  - libwhisper           (the whisper.cpp core)
  - libggml              (ggml dispatcher)
  - libggml-base         (ggml base ops)
  - libggml-cpu          (CPU backend — always present, the CT2-CPU-free floor)
  - libggml-vulkan       (Vulkan backend — present ONLY in the Vulkan wheel; a
                          GGML_BACKEND_DL MODULE the ggml registry dlopen-s at
                          runtime, skipped gracefully when the Vulkan loader is
                          absent)
  - libgomp / vcomp      (OpenMP runtime the ggml-cpu backend links)

ggml discovers its backends (``ggml_backend_load_all`` / ``_best``) by scanning
the directory the loaded ggml libs live in, so the collect_all placement — all
of them flattened next to the extension in the frozen tree — is exactly where
the runtime expects them.  The _engine seam (anki_miner/services/asr/_engine.py)
globs the same set of dirs (package dir, site root, ``*.libs``) to find
ggml-vulkan, and PyInstaller's collect_all reproduces that adjacency.

EXCLUDE the Vulkan loader (libvulkan.so.1 / vulkan-1.dll): it must NOT be frozen
into the bundle.  The wheel build already strips it (auditwheel/delvewheel
``--exclude``), so it should not be present to collect — but a defensive filter
here guarantees that even if a future wheel re-vendors it, the system/driver
loader (the user's actual GPU driver) wins at runtime instead of a stale copy.

Harmless when pywhispercpp is absent: ``collect_all`` on a missing package
returns empty lists rather than raising (the Intel-mac / no-[asr] builds), so
this hook is a no-op there.  (The pywhispercpp/ggml libs are stripped from the
lean .deb stage tree separately; see release.yml.)
"""

import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("pywhispercpp")

# Defensive: drop the Vulkan loader if a wheel ever vendors it. The loader is the
# ICD-dispatching shim that must come from the system / GPU driver at runtime, so
# a frozen copy would shadow the driver's. Matched case-insensitively by basename
# (libvulkan.so / libvulkan.so.1 on Linux, vulkan-1.dll on Windows).
_VULKAN_LOADER_NAMES = ("libvulkan.so", "vulkan-1.dll")


def _is_vulkan_loader(dest: str) -> bool:
    base = os.path.basename(dest).lower()
    return any(base == n or base.startswith(n + ".") for n in _VULKAN_LOADER_NAMES)


binaries = [(src, dest) for (src, dest) in binaries if not _is_vulkan_loader(dest)]
