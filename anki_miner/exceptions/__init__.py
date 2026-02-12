"""Custom exceptions for Anki Miner."""

from .anki import AnkiConnectionError, CardCreationError, DeckNotFoundError, NoteTypeNotFoundError
from .base import AnkiMinerException
from .media import FFmpegError, MediaExtractionError, SubtitleParseError
from .validation import SetupError, ValidationError

__all__ = [
    "AnkiMinerException",
    "ValidationError",
    "SetupError",
    "AnkiConnectionError",
    "DeckNotFoundError",
    "NoteTypeNotFoundError",
    "CardCreationError",
    "MediaExtractionError",
    "SubtitleParseError",
    "FFmpegError",
]
