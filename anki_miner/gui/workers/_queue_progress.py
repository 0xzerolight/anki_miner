"""Shared formatter turning pipeline progress into one truthful line of text.

Used by the YouTube, audiobook, and reading queue workers to translate
``ProgressCallback`` invocations from ``EpisodeProcessor`` into per-row
``(idx, label)`` signal emissions, and by ``MiningTabBase`` to word the
single-run status line the same way.

The adapter emits **text only**. It used to also emit a percentage for the
item, mapped into a band so a preceding phase and the mining sweep composed
into one continuous fill. That number was never real: it came from hard-coded
stage weights, so it raced through short stages and sat on long ones. What is
genuinely known -- which stage of how many, and the true count inside that
stage -- is what the label now says, and the queue bar counts finished items
instead.

Internal-but-tested: this private module (leading underscore) has no public facade --
``tests/unit/test_honest_stage_progress.py`` imports it directly. The underscore stays
and the module path is a stable test surface; do not rename it.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QCoreApplication

from anki_miner.utils.i18n import tr_format


class QueueMiningProgressAdapter:
    """``ProgressCallback`` shim that turns pipeline progress into one row label.

    The queue item ``idx`` is baked into the emit signature so the tab can
    route updates to the right list row.
    """

    def __init__(
        self,
        idx: int,
        emit: Callable[[int, str], None],
    ) -> None:
        self._idx = idx
        self._emit = emit
        self._position = ""
        self._name = ""
        self._total = 0
        self._last_label = ""

    def _publish(self, detail: str) -> None:
        """Emit position, stage name and detail, dropping repeated parts.

        Pipeline item strings are self-prefixed with their own phase
        ("Extracting media: 語"), so printing the stage name in front of them as
        well produced "Preparing page images: Expression audio: 語" (issue #1).
        A detail that already opens with the stage name therefore stands in for
        it rather than being appended to it.
        """
        if self._name and detail.startswith(self._name):
            body = detail
        elif detail and self._name:
            body = f"{self._name} · {detail}"
        else:
            body = detail or self._name
        parts = [p for p in (self._position, body) if p]
        self._emit(self._idx, " · ".join(parts))

    def on_stage(self, index: int, total: int, name: str) -> None:
        """Record and announce the pipeline stage this item has reached."""
        self._position = tr_format(
            QCoreApplication.translate("QueueMiningProgressAdapter", "Stage %1 of %2"),
            index,
            total,
        )
        self._name = name
        # A new stage supersedes the previous stage's item detail and count.
        self._last_label = ""
        self._total = 0
        self._publish("")

    def on_start(self, total: int, description: str) -> None:
        self._total = max(0, total)
        self._last_label = description
        self._publish(description)

    def on_progress(self, current: int, item_description: str) -> None:
        # Fall back to the LAST label seen when the item string is empty.
        if item_description:
            self._last_label = item_description
        label = self._last_label
        # The count is shown only when the stage declared a real total. There is
        # no fallback denominator: an unknown total stays unstated.
        if self._total > 0:
            label = tr_format(
                QCoreApplication.translate("QueueMiningProgressAdapter", "%1 (%2 of %3)"),
                label,
                current,
                self._total,
            )
        self._publish(label)

    def on_complete(self) -> None:
        # Silent. A stage finishing is not the item finishing, and saying
        # "Complete" here made every stage boundary look like the end of the
        # item. The queue owns the item's verdict via ``item_finished``.
        return

    def on_error(self, item_description: str, error_message: str) -> None:
        # No-op. Per-item mining failures surface as exceptions that the
        # queue worker's except clause routes to ``item_finished``.
        # Emitting progress here would re-trigger a busy animation after
        # mining already failed.
        return
