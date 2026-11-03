"""Anki and AnkiConnect related exceptions."""

from .base import AnkiMinerException


class AnkiConnectionError(AnkiMinerException):
    """Raised when cannot connect to AnkiConnect."""

    pass


class AnkiBusyError(AnkiConnectionError):
    """AnkiConnect refused an action because Anki's collection is busy (a sync).

    AnkiConnect refuses before it runs the action, so nothing was written and
    the same call succeeds once Anki is done. Not a setup problem.
    """
