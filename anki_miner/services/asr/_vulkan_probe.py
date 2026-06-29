"""Standalone subprocess probe for the ggml-vulkan device count.

This module is spawned as a *child process* by
:func:`anki_miner.services.asr._engine.vulkan_device_count` (and, in a frozen
bundle, re-entered via the ``ANKI_MINER_ASR_VULKAN_PROBE`` env var in
``gui.app.main()``). It does the one thing that is unsafe to do in-process: a
cold ``ctypes`` call into ggml-vulkan. A broken Vulkan driver can ``abort()``
the process uncatchably — isolating that here means the abort kills only this
child, and the parent reads a clean ``0`` (CPU) from our stdout.

Contract: print a single integer to stdout and exit 0. On ANY error or a
missing ggml-vulkan lib, print ``0`` and exit 0 — never a nonzero exit, never a
traceback on stdout — so the parent always parses a clean number.
"""

import sys


def main() -> int:
    """Print the ggml-vulkan device count to stdout; always exit 0.

    Returns 0 (the exit code) unconditionally. The *device count* is what's
    printed; a missing lib or any failure prints ``0``.
    """
    try:
        import ctypes  # noqa: PLC0415  (intentional function-local import)

        from anki_miner.services.asr._engine import _find_ggml_vulkan_lib

        lib_path = _find_ggml_vulkan_lib()
        if lib_path is None:
            print("0")
            return 0

        lib = ctypes.CDLL(str(lib_path))
        get_device_count = lib.ggml_backend_vk_get_device_count
        get_device_count.restype = ctypes.c_int
        get_device_count.argtypes = []
        count = int(get_device_count())
        print(count)
        return 0
    except Exception:  # noqa: BLE001 — any failure means "no usable Vulkan device"
        print("0")
        return 0


if __name__ == "__main__":
    sys.exit(main())
