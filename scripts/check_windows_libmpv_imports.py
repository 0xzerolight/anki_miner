#!/usr/bin/env python3
"""Fail-closed audit of the vendored Windows ``libmpv-2.dll`` import table.

A *present* ``libmpv-2.dll`` whose ``LoadLibrary`` fails (ctypes.CDLL raising
WinError 126) is the confirmed field bug: it imported ``vulkan-1.dll`` at LOAD
time (a non-delay import), and that loader is absent on machines without a
Vulkan-capable GPU driver (fresh installs, VMs, generic display drivers). The
release ``mpv`` smoke never caught it because ``windows-latest`` ships
``vulkan-1.dll`` in System32.

This audit runs at *vendor* time (on Linux, via ``objdump`` — no Windows needed)
and mirrors the Linux ``NEEDED``-against-allowlist audit in
``.github/workflows/vendor-libmpv.yml``. It enumerates the DLL's LOAD-TIME
imports (delay-loaded imports never fail ``LoadLibrary`` and are ignored) and
fails if any is neither:

  * a system DLL guaranteed present on the minimum-supported Windows edition
    (``_WINDOWS_SYSTEM_BASELINE``), nor
  * a dependency we DELIBERATELY ship in the bundle (``_BUNDLED_DEPS``).

So a future zhongfly build that adds a NEW hard dependency (e.g. ``libplacebo``)
fails the vendor job LOUDLY instead of silently shipping another WinError-126
bomb. It does NOT prove the DLL loads on a bare machine — that needs a real
``ctypes.CDLL`` on a Vulkan-less Windows image — but it catches the whole class
of "unhandled load-time dependency" regressions statically.

Usage:  python scripts/check_windows_libmpv_imports.py <path-to-libmpv-2.dll>
"""

from __future__ import annotations

import re
import subprocess
import sys

# System DLLs guaranteed on every supported Windows edition (Win10+). Matched
# case-insensitively. ``api-ms-win-*`` (the API-set / UCRT forwarders) are
# always present on Win10+ and are covered by the prefix rule below, not listed
# individually.
_WINDOWS_SYSTEM_BASELINE = {
    "advapi32.dll",
    "avicap32.dll",
    "avrt.dll",
    "bcrypt.dll",
    "bcryptprimitives.dll",
    "crypt32.dll",
    "d2d1.dll",
    "dwmapi.dll",
    "dwrite.dll",
    "gdi32.dll",
    "imm32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "opengl32.dll",
    "ntdll.dll",
    "secur32.dll",
    "setupapi.dll",
    "shcore.dll",
    "shell32.dll",
    "shlwapi.dll",
    "user32.dll",
    "uxtheme.dll",
    "version.dll",
    "winmm.dll",
    "wldap32.dll",
    "ws2_32.dll",
}

# Non-system load-time deps we DELIBERATELY ship next to libmpv-2.dll at the
# _MEIPASS root (see the "Bundle the Vulkan loader" step in release.yml). The
# Vulkan loader is redistributable (Apache-2.0); a loader with zero ICDs is
# harmless because the player forces vo=libmpv + OpenGL and never selects the
# Vulkan VO.
_BUNDLED_DEPS = {
    "vulkan-1.dll",
}

# api-ms-win-* / ext-ms-win-* are API-set forwarders resolved by the OS loader
# on Win10+; always allowed.
_APISET_PREFIXES = ("api-ms-win-", "ext-ms-win-")


def load_time_imports(dll_path: str) -> list[str]:
    """Return the DLL names in the LOAD-TIME import table (delay imports excluded).

    ``objdump -p`` prints "The Import Tables" (load-time) and, separately, "The
    Delay Import Tables". Only the former can fail ``LoadLibrary``, so we read
    names until the delay section begins.
    """
    out = subprocess.run(
        ["objdump", "-p", dll_path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names: list[str] = []
    in_delay = False
    for line in out.splitlines():
        if "Delay Import Tables" in line:
            in_delay = True
            continue
        if in_delay:
            continue
        m = re.match(r"\s*DLL Name:\s*(\S+)", line)
        if m:
            names.append(m.group(1))
    return names


def audit(dll_path: str) -> list[str]:
    """Return the list of unexpected load-time imports (empty == clean)."""
    offenders: list[str] = []
    for name in load_time_imports(dll_path):
        lower = name.lower()
        if lower in _WINDOWS_SYSTEM_BASELINE:
            continue
        if lower in _BUNDLED_DEPS:
            continue
        if lower.startswith(_APISET_PREFIXES):
            continue
        offenders.append(name)
    return offenders


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    dll_path = argv[1]
    imports = load_time_imports(dll_path)
    offenders = audit(dll_path)
    print(f"Audited {dll_path}: {len(imports)} load-time imports")
    if offenders:
        print(
            "UNEXPECTED load-time imports (would fail LoadLibrary where absent, "
            "and are neither in the Windows baseline nor bundled):"
        )
        for name in sorted(offenders):
            print(f"  - {name}")
        print(
            "Fix: ship the dependency in the Windows closure (see release.yml) "
            "and add it to _BUNDLED_DEPS, OR drop the mpv feature that pulls it."
        )
        return 1
    print("OK: every load-time import is a Windows baseline DLL or a bundled dependency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
