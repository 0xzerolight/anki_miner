"""Anki and AnkiConnect related exceptions."""

from .base import AnkiMinerException


class AnkiConnectionError(AnkiMinerException):
    """Raised when cannot connect to AnkiConnect."""

    pass


class DeckNotFoundError(AnkiMinerException):
    """Raised when the target Anki deck is not found."""

    pass


class NoteTypeNotFoundError(AnkiMinerException):
    """Raised when the specified note type is not found."""

    pass


class CardCreationError(AnkiMinerException):
    """Raised when card creation fails."""

    pass
