"""Utility functions for the GUI layer."""

from .config_manager import GUIConfigManager
from .fonts import make_scaled_font
from .recent_files import RecentFilesManager

__all__ = [
    "GUIConfigManager",
    "make_scaled_font",
    "RecentFilesManager",
]
