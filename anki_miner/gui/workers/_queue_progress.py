"""Shared progress adapter for sequential queue workers.

Used by both the YouTube and audiobook queue workers to translate
``ProgressCallback`` invocations from ``EpisodeProcessor`` into per-row
``(idx, label, pct)`` signal emissions.
"""

from __future__ import annotations

from collections.abc import Callable


class QueueMiningProgressAdapter:
    """``ProgressCallback`` shim that translates progress into ``(idx, label, pct)`` emits with ``idx`` baked in.

    The queue item ``idx`` is baked into the emit signature so the tab can
    route updates to the right list row.
    """

    def __init__(self, idx: int, emit: Callable[[int, str, int], None]) -> None:
        self._idx = idx
        self._emit = emit
        self._total = 1
        self._desc = ""

    def on_start(self, total: int, description: str) -> None:
        self._total = max(1, total)
        self._desc = description
        self._emit(self._idx, description, 0)

    def on_progress(self, current: int, item_description: str) -> None:
        pct = int(round(100 * current / self._total))
        label = f"{self._desc}: {item_description}" if self._desc else item_description
        self._emit(self._idx, label, pct)

    def on_complete(self) -> None:
        self._emit(self._idx, self._desc or "Complete", 100)

    def on_error(self, item_description: str, error_message: str) -> None:
        # No-op. Per-item mining failures surface as exceptions that the
        # queue worker's except clause routes to ``item_finished``.
        # Emitting progress here would re-trigger a busy animation after
        # mining already failed.
        return
