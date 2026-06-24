"""Subtitle retiming exceptions."""

from .base import AnkiMinerException


class SubtitleRetimeError(AnkiMinerException):
    """Base class for subtitle retiming failures."""

    pass


class AlassNotFoundError(SubtitleRetimeError):
    """Raised when the alass executable cannot be located.

    A specific subclass so callers can catch the "binary missing" case and
    steer the user to install alass or set the path in Settings → Subtitles.
    """

    pass
