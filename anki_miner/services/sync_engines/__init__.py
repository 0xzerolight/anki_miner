"""Subtitle sync engines and their shared result type.

Each engine synchronizes one subtitle file against one reference and reports
what it did in a :class:`SyncResult`. Engines never raise for content reasons
and never decide whether their output is *good* — that is the validator's job
(:mod:`anki_miner.services.sync_validator`); an engine's ``ok`` only means the
tool ran to completion and produced an output file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SyncResult"]


@dataclass(frozen=True)
class SyncResult:
    """What a sync engine did for one file.

    ``block_shifts_seconds`` is alass's parsed per-block shift list (one entry
    per ``shifted block`` stdout line); ffsubsync reports a single
    ``offset_seconds`` + ``framerate_scale`` instead. ``warnings`` carries
    engine-emitted suspicion signals (negative timestamps, FPS-ratio guesses)
    for the validator to weigh.
    """

    ok: bool
    engine: str
    offset_seconds: float | None = None
    framerate_scale: float | None = None
    block_shifts_seconds: tuple[float, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""
