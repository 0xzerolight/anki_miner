"""Data models for media files."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaData:
    """Media data (screenshot and audio) for a vocabulary word."""

    screenshot_path: Path | None = None
    audio_path: Path | None = None
    screenshot_filename: str | None = None
    audio_filename: str | None = None

    @property
    def has_screenshot(self) -> bool:
        """Check if screenshot exists."""
        return self.screenshot_path is not None and self.screenshot_path.exists()

    @property
    def has_audio(self) -> bool:
        """Check if audio exists."""
        return self.audio_path is not None and self.audio_path.exists()

    @property
    def has_any_media(self) -> bool:
        """Check if any media exists."""
        return self.has_screenshot or self.has_audio

    def __str__(self) -> str:
        parts = []
        if self.has_screenshot:
            parts.append(f"Screenshot: {self.screenshot_filename}")
        if self.has_audio:
            parts.append(f"Audio: {self.audio_filename}")
        return ", ".join(parts) if parts else "No media"
