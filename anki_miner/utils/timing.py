"""Lightweight wall-clock instrumentation for pipeline phases.

The mining pipeline had no per-phase timing at all — only the whole-run
``ProcessingResult.elapsed_time`` — so costs like the two-ffmpeg-per-word
extraction (OVH-049, media_extractor.py) stayed unquantified. ``timed_phase``
wraps one phase call and logs its duration to ``anki_miner.log`` at INFO;
developer-facing only, never surfaced through the presenter.

Phase granularity only: never wrap per-word hot loops — the log line itself
would become the overhead.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def timed_phase(name: str, log: logging.Logger | None = None) -> Iterator[None]:
    """Log the wall-clock duration of one pipeline phase at INFO.

    Logs in a ``finally`` so early returns, cancels, and exceptions still
    record the elapsed time. ``perf_counter`` (monotonic), not ``time.time``.

    Args:
        name: Phase label, e.g. ``"extract"`` — rendered as ``[timing] extract: 1.23s``.
        log: Logger to emit on; defaults to this module's logger. Passing the
            caller's module logger keeps the log line attributed to the phase's
            own module.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        (log or logger).info("[timing] %s: %.2fs", name, time.perf_counter() - start)
