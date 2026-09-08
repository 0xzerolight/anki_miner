"""Exceptions raised by the Manga OCR (mokuro) tool."""

from .base import AnkiMinerException


class MokuroError(AnkiMinerException):
    """A mokuro run failed: nonzero exit, timeout, or no ``.mokuro`` written."""


class MokuroNotFoundError(MokuroError):
    """The mokuro executable cannot be located/run.

    A specific subclass so the queue worker can stop the whole run and steer
    the user to Settings → Transcription & Alignment → Manga OCR.
    """
