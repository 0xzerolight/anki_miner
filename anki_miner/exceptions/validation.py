"""Validation-related exceptions."""

from .base import AnkiMinerException


class SetupError(AnkiMinerException):
    """Raised when setup checks fail (missing dependencies, etc)."""

    pass
