"""Custom exceptions for Anki Miner."""

from .anki import AnkiConnectionError
from .base import AnkiMinerException
from .cancel import OperationCancelled
from .media import SubtitleParseError
from .subtitle import AlassNotFoundError, SubtitleRetimeError
from .validation import DownloadFailed, SetupError
from .youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    NoJapaneseSubtitlesError,
    NoSourceSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)

__all__ = [
    "AnkiMinerException",
    "SetupError",
    "DownloadFailed",
    "OperationCancelled",
    "AnkiConnectionError",
    "SubtitleParseError",
    "AlassNotFoundError",
    "SubtitleRetimeError",
    "BotDetectionError",
    "CookieDatabaseLockedError",
    "FfmpegNotFoundError",
    "NoJapaneseSubtitlesError",
    "NoSourceSubtitlesError",
    "VideoTooLongError",
    "YouTubeFetchError",
    "YtdlpNotFoundError",
]
