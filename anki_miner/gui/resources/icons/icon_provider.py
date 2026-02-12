"""Icon management system for Anki Miner GUI.

This module provides centralized icon loading and management.
Currently returns empty strings (no icons) - can be extended to support
SVG icons in the future.
"""


class IconProvider:
    """Centralized icon management for the application.

    Provides a minimal interface for icon retrieval.
    All icons currently return empty strings.
    """

    @classmethod
    def get_icon(cls, name: str, size: int = 20) -> str:
        """Get an icon character by name.

        Args:
            name: Icon name
            size: Icon size (not used, kept for API compatibility)

        Returns:
            Empty string (icons disabled)
        """
        return ""
