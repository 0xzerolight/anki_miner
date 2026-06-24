"""Custom exceptions for Anki Miner."""

from .anki import AnkiConnectionError
from .base import AnkiMinerException
from .media import SubtitleParseError
from .subtitle import AlassNotFoundError, SubtitleRetimeError
from .validation import SetupError
from .youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)

__all__ = [
    "AnkiMinerException",
    "SetupError",
    "AnkiConnectionError",
    "SubtitleParseError",
    "AlassNotFoundError",
    "SubtitleRetimeError",
    "BotDetectionError",
    "CookieDatabaseLockedError",
    "FfmpegNotFoundError",
    "VideoTooLongError",
    "YouTubeFetchError",
    "YtdlpNotFoundError",
]
