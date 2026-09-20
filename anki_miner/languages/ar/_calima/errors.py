"""Errors raised while loading a morphology database or analysing (ported, see ``__init__``)."""

from __future__ import annotations

__all__ = ["AnalyzerError", "DatabaseParseError", "MorphologyError"]


class MorphologyError(Exception):
    """Base class for every error this package raises."""


class DatabaseParseError(MorphologyError):
    """The morphology database file does not have the expected layout."""


class AnalyzerError(MorphologyError):
    """The analyzer was given something it cannot work with."""
