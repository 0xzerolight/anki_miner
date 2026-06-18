"""Windows console-window suppression for spawned subprocesses (Issue #79).

On Windows, a windowed (no-console) GUI app that spawns a console binary
(ffmpeg, ffprobe, yt-dlp, powershell) makes the OS allocate a fresh console for
each child, flashing a ``cmd.exe`` window that steals focus. During batch mining
this fires dozens of times per run. ``CREATE_NO_WINDOW`` runs the console child
with no console window at all; stdout/stderr are already captured via ``PIPE`` at
every call site, so nothing is lost. No-op off Windows.
"""

import subprocess
import sys
from typing import Any

# subprocess.CREATE_NO_WINDOW exists only on Windows Python builds; fall back to
# its documented numeric value so this module imports + unit-tests on non-Windows
# CI (e.g. with sys.platform monkeypatched to "win32").
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

__all__ = ["no_window_kwargs"]


def no_window_kwargs() -> dict[str, Any]:
    """subprocess kwargs that suppress the Windows console window; ``{}`` off Windows.

    Spread into every spawn: ``subprocess.run(cmd, ..., **no_window_kwargs())``.
    """
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}
