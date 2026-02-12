"""Validation-related exceptions."""

from .base import AnkiMinerException


class ValidationError(AnkiMinerException):
    """Raised when validation fails."""

    pass


class SetupError(AnkiMinerException):
    """Raised when setup checks fail (missing dependencies, etc)."""

    pass
