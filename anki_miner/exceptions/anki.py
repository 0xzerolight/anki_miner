"""Anki and AnkiConnect related exceptions."""

from .base import AnkiMinerException


class AnkiConnectionError(AnkiMinerException):
    """Raised when cannot connect to AnkiConnect."""

    pass
