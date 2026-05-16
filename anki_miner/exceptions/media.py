"""Media and subtitle processing exceptions."""

from .base import AnkiMinerException


class SubtitleParseError(AnkiMinerException):
    """Raised when subtitle parsing fails."""

    pass
