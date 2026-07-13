"""Queue-worker base for the file-processing tools (subtitle gen / retime / condense).

Hoists the byte-identical 5-signal contract and the per-file queue loop shared by
:class:`~anki_miner.gui.workers.subtitle_gen_worker.SubtitleGenWorker`,
:class:`~anki_miner.gui.workers.subtitle_retime_worker.SubtitleRetimeWorker`, and
:class:`~anki_miner.gui.workers.condense_worker.CondenseWorker`.

Signal contract (inherited by every subclass — PyQt6 propagates base-class
signals, so the per-worker declarations were removed):
    ``file_started(int)``                  — start of each item (idx)
    ``file_progress(int, int, str)``       — (idx, pct 0-100, message)
    ``file_finished(int, object, object)`` — (idx, out_path|None, error_str|None)
    ``file_skipped(int, object)``          — (idx, out_path) when output exists and overwrite is False
    ``queue_finished()``                   — emitted once after the last item

Subclasses implement two hooks and keep their own per-item logic:
    ``_queue_items()``           — the ordered items to process.
    ``_process_item(idx, item)`` — per-item work (resolve output, skip-or-run).
        Must not raise, EXCEPT for a member of :attr:`_FATAL_QUEUE_EXCEPTIONS`,
        which the loop converts into a per-item error plus a whole-queue stop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker


class FileQueueWorker(CancellableWorker):
    """Process an ordered list of items, emitting per-item progress signals.

    ``run()`` walks :meth:`_queue_items`, honouring cancellation between items,
    and always emits :attr:`queue_finished` afterwards. A member of
    :attr:`_FATAL_QUEUE_EXCEPTIONS` raised by :meth:`_process_item` stops the
    queue (every remaining item would fail the same way) after emitting the
    triggering item's error — distinct from a user cancel (``is_cancelled``
    stays False).
    """

    #: Emitted at the start of each item; argument is the 0-based item index.
    file_started = pyqtSignal(int)
    #: (idx, pct 0-100, message) — progress within a single item.
    file_progress = pyqtSignal(int, int, str)
    #: (idx, out_path|None, error_str|None) — outcome for each item.
    file_finished = pyqtSignal(int, object, object)
    #: (idx, out_path) — emitted when the output already exists and overwrite is False.
    file_skipped = pyqtSignal(int, object)
    #: Emitted once after all items have been processed (or skipped / errored).
    queue_finished = pyqtSignal()

    #: Exceptions that abort the WHOLE queue when raised by :meth:`_process_item`
    #: (a missing tool/encoder dooms every remaining item). The loop reports the
    #: triggering item's error, then stops WITHOUT touching ``_cancel_event`` —
    #: ``is_cancelled`` must stay False so callers can tell a tool error from a
    #: user cancel. Subclasses override, e.g. ``(AlassNotFoundError,)``. The
    #: default empty tuple means no exception stops the queue.
    _FATAL_QUEUE_EXCEPTIONS: tuple[type[BaseException], ...] = ()

    def __init__(self, parent=None) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        # Set when a _FATAL_QUEUE_EXCEPTIONS member fires: stops the queue
        # without poisoning is_cancelled (a tool error, not a user cancel).
        self._stop_queue = False

    def run(self) -> None:
        """Process every queued item on the background thread."""
        try:
            self._process_queue()
        finally:
            self.queue_finished.emit()

    def _process_queue(self) -> None:
        for idx, item in enumerate(self._queue_items()):
            if self.is_cancelled or self._stop_queue:
                break

            self.file_started.emit(idx)

            try:
                self._process_item(idx, item)
            except self._FATAL_QUEUE_EXCEPTIONS as exc:
                # Missing tool/encoder affects every remaining item — report this
                # item's failure, then flag the queue to stop. Do NOT touch
                # _cancel_event: is_cancelled must stay False so callers can tell
                # a tool error from a user cancel.
                self.file_finished.emit(idx, None, str(exc))
                self._stop_queue = True

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _queue_items(self) -> Sequence[Any]:
        """Return the ordered items to process."""
        raise NotImplementedError

    def _process_item(self, idx: int, item: Any) -> None:
        """Process one item (resolve output, skip-or-run); see class docstring."""
        raise NotImplementedError
