"""Cooperative-cancellation exception."""

from .validation import SetupError


class OperationCancelled(SetupError):
    """Raised when the user cancelled a long-running operation.

    Deliberately NOT named ``CancelledError``: that name belongs to
    ``asyncio``/``concurrent.futures``, where it is a ``BaseException``, so the
    two would be confused at every ``except``.

    Subclasses ``SetupError`` rather than ``AnkiMinerException`` so the cancel
    raise sites migrated to it stay caught by every existing
    ``except SetupError`` -- notably ``pitch_accent_service._pitch_rows``'s
    re-raise guard, which would otherwise rewrap a cancel as a fake error.
    Reparenting to ``AnkiMinerException`` is a follow-up, once those handlers
    grow an explicit ``except OperationCancelled`` arm.
    """

    pass
