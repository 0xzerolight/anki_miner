"""Media and subtitle processing exceptions."""

from .base import AnkiMinerException


class MediaExtractionError(AnkiMinerException):
    """Raised when media extraction fails."""

    pass


class SubtitleParseError(AnkiMinerException):
    """Raised when subtitle parsing fails."""

    pass


class FFmpegError(AnkiMinerException):
    """Raised when ffmpeg operations fail."""

    pass
