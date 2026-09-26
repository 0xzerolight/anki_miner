"""Cooperative-cancellation exception."""

from __future__ import annotations

from typing import Callable

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


def raise_if_cancelled(cancel_check: Callable[[], bool] | None, message: str = "Import cancelled") -> None:
    """Raise :class:`OperationCancelled` when ``cancel_check`` reports cancellation.

    A no-op when ``cancel_check`` is ``None``. The shared body behind the
    module-level ``_raise_if_cancelled`` copies every long-running importer
    used to define for itself.
    """
    if cancel_check is not None and cancel_check():
        raise OperationCancelled(message)
