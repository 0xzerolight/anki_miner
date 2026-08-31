"""Validation-related exceptions."""

from .base import AnkiMinerException


class SetupError(AnkiMinerException):
    """Raised when setup checks fail (missing dependencies, etc)."""

    pass


class DownloadFailed(SetupError):
    """Raised when a download never completed: transport or HTTP failure.

    Subclasses ``SetupError`` (the ``OperationCancelled`` precedent) so every
    existing ``except SetupError`` keeps catching it and no handler had to
    change. It exists for the callers that must tell "the network was not
    there" from "the bytes were wrong": ``scripts/fetch_language_pack_seeds.py``
    skips a release bundle smoke on the first and fails the release on the
    second. Bytes that arrived but do not add up -- a size-cap abort, a
    truncated body, a checksum mismatch -- stay plain ``SetupError``.
    """

    pass
