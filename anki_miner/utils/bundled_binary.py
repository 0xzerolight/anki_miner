"""Shared PyInstaller-bundle helpers for executable resolvers.

The alass / ffmpeg / yt-dlp resolvers each locate a binary that may be shipped
inside a PyInstaller frozen bundle. Two primitives are identical across all
three: detecting the frozen state (and its ``_MEIPASS`` root) and mapping a base
name to the platform-specific executable name. They live here so the resolvers
call one implementation instead of re-declaring it.

The frozen-detection idiom mirrors ``anki_miner.gui.resources.get_resource_dir``.
"""

import sys

__all__ = ["frozen_state", "bundled_name"]


def frozen_state() -> tuple[bool, str | None]:
    """Return (is_frozen, _MEIPASS) using the same idiom as get_resource_dir()."""
    frozen = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")
    meipass = getattr(sys, "_MEIPASS", None) if frozen else None
    return frozen, meipass


def bundled_name(base: str) -> str:
    """Return the platform-specific executable name (``.exe`` on Windows)."""
    return f"{base}.exe" if sys.platform == "win32" else base
