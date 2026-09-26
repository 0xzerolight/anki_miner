"""Custom exceptions for Anki Miner."""

from .anki import AnkiConnectionError
from .base import AnkiMinerException
from .cancel import OperationCancelled, raise_if_cancelled
from .media import SubtitleParseError
from .mokuro import MokuroError, MokuroNotFoundError
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
    "raise_if_cancelled",
    "AnkiConnectionError",
    "SubtitleParseError",
    "MokuroError",
    "MokuroNotFoundError",
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
