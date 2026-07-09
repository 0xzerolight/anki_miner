"""Shared progress adapter for sequential queue workers.

Used by the YouTube, audiobook, and reading queue workers to translate
``ProgressCallback`` invocations from ``EpisodeProcessor`` into per-row
``(idx, label, pct)`` signal emissions.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QCoreApplication


class QueueMiningProgressAdapter:
    """``ProgressCallback`` shim that translates progress into ``(idx, label, pct)`` emits with ``idx`` baked in.

    The queue item ``idx`` is baked into the emit signature so the tab can
    route updates to the right list row.

    ``band`` maps the pipeline's 0-100 sweep into a sub-range of the item's
    overall percent, so a preceding phase (e.g. the YouTube download at
    0-30) and the mining sweep compose into one continuous fill instead of
    the bar restarting from 0 mid-item.
    """

    def __init__(
        self,
        idx: int,
        emit: Callable[[int, str, int], None],
        band: tuple[int, int] = (0, 100),
    ) -> None:
        self._idx = idx
        self._emit = emit
        self._band = band
        self._total = 1
        self._desc = ""
        self._last_label = ""

    def _band_pct(self, frac: float) -> int:
        start, end = self._band
        return start + int(round((end - start) * frac))

    def on_start(self, total: int, description: str) -> None:
        self._total = max(1, total)
        self._desc = description
        self._last_label = description
        self._emit(self._idx, description, self._band_pct(0.0))

    def on_progress(self, current: int, item_description: str) -> None:
        pct = self._band_pct(current / self._total)
        # Every pipeline item string is already self-prefixed with its stage
        # ("Extracting media: X", "Expression audio: 語", ...), so emit it as-is.
        # Gluing on self._desc — frozen at stage 1, since StageWeightedProgress
        # forwards on_start only once — produced double prefixes like
        # "Preparing page images: Expression audio: 語" (issue #1). Fall back to
        # the LAST label seen — not the frozen stage-1 desc — when the item
        # string is empty (e.g. finish()'s terminal ``on_progress(100, "")``).
        if item_description:
            self._last_label = item_description
            label = item_description
        else:
            label = self._last_label
        self._emit(self._idx, label, pct)

    def on_complete(self) -> None:
        self._emit(
            self._idx,
            QCoreApplication.translate("QueueMiningProgressAdapter", "Complete"),
            self._band[1],
        )

    def on_error(self, item_description: str, error_message: str) -> None:
        # No-op. Per-item mining failures surface as exceptions that the
        # queue worker's except clause routes to ``item_finished``.
        # Emitting progress here would re-trigger a busy animation after
        # mining already failed.
        return
