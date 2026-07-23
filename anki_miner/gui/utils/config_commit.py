"""Outcome model for synchronous configuration commits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigCommitResult:
    """Whether a config reached disk and completed in-process refresh."""

    persisted: bool
    refreshed: bool
    error: Exception | None = None

    @classmethod
    def committed(cls) -> ConfigCommitResult:
        return cls(persisted=True, refreshed=True)

    @classmethod
    def pre_save_failure(cls, error: Exception) -> ConfigCommitResult:
        return cls(persisted=False, refreshed=False, error=error)

    @classmethod
    def post_save_failure(cls, error: Exception) -> ConfigCommitResult:
        return cls(persisted=True, refreshed=False, error=error)


class ConfigCommitError(RuntimeError):
    """Commit failure carrying the durable boundary reached."""

    def __init__(self, result: ConfigCommitResult) -> None:
        self.result = result
        message = str(result.error) if result.error is not None else "Configuration commit failed"
        super().__init__(message)
