"""Custom exceptions for Anki Miner."""

from .anki import AnkiConnectionError
from .base import AnkiMinerException
from .media import SubtitleParseError
from .validation import SetupError
from .youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    VideoTooLongError,
    YouTubeFetchError,
)

__all__ = [
    "AnkiMinerException",
    "SetupError",
    "AnkiConnectionError",
    "SubtitleParseError",
    "BotDetectionError",
    "CookieDatabaseLockedError",
    "FfmpegNotFoundError",
    "VideoTooLongError",
    "YouTubeFetchError",
]
