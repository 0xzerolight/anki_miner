"""YouTube fetcher exceptions."""

from .base import AnkiMinerException


class YouTubeFetchError(AnkiMinerException):
    """Base class for YouTube fetch failures."""

    pass


class FfmpegNotFoundError(AnkiMinerException):
    """Raised when ffmpeg cannot be located during preflight."""

    pass


class BotDetectionError(YouTubeFetchError):
    """Raised when yt-dlp hits the 'sign in to confirm' anti-bot flow."""

    pass


class CookieDatabaseLockedError(YouTubeFetchError):
    """Raised when yt-dlp cannot read a cookies-from-browser database."""

    pass


class VideoTooLongError(YouTubeFetchError):
    """Raised when a video's duration exceeds the configured maximum."""

    pass
