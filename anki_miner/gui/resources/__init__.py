"""GUI resources (icons, styles)."""

import sys
from pathlib import Path

__all__ = ["get_resource_dir"]


def get_resource_dir() -> Path:
    """Get the path to the GUI resources directory.

    In a normal Python environment, this resolves relative to this file.
    In a PyInstaller frozen bundle, it resolves relative to sys._MEIPASS.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "anki_miner" / "gui" / "resources"
    return Path(__file__).parent
