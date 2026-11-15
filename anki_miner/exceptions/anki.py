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


class AnkiActionError(AnkiConnectionError):
    """AnkiConnect answered the action with an error of its own.

    Anki was reached and replied, unlike a dropped connection or an unreadable
    reply. ``anki_error`` is AnkiConnect's ``error`` text as it sent it.
    """

    def __init__(self, message: str, anki_error: str) -> None:
        super().__init__(message)
        self.anki_error = anki_error
