"""Shared base for the three sequential mining queue workers.

:class:`YouTubeQueueWorker`, :class:`ReadingQueueWorker`, and
:class:`AudiobookQueueWorker` are structurally the same worker: each drives a
frozen snapshot of queue items through mining one at a time, emits the identical
four-signal shape, validates the processor-XOR-factory constructor contract, and
opens ``run()`` with the same schema-staleness pre-loop gate + deferred factory
build. This base owns that spine so the three subclasses carry only their
per-item body (``_run_item`` / ``_mine_one``) and per-tab extras.

Signal shape (declared here, inherited by every subclass — PyQt6 propagates
signals to subclasses):

* ``item_started(int)`` — idx, fired once before an item is mined. Items removed
  mid-run via :meth:`skip_item` are silently skipped: no signals for them.
* ``item_progress(int, str, int)`` — idx, label, pct.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Fires exactly once per item that runs.
* ``queue_finished()`` — fires once at the bottom of ``run()`` unless a subclass
  ``_run_item`` requested an early return (YouTube's mid-fetch cancellation).

The skip channel lives here so all three workers share it; without it a GUI-side
removal alone would still mine the item, because ``run()`` iterates the frozen
constructor snapshot, not the live GUI queue.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Generic, TypeVar

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import ProcessorOwningWorker

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)

# Queue item type — the concrete dataclass a subclass drives (all three are
# ``@dataclass(eq=False)`` so the skip set keys on identity).
ItemT = TypeVar("ItemT")


class SequentialQueueWorker(ProcessorOwningWorker, Generic[ItemT]):
    """Base for workers that mine a queue of items one at a time.

    Subclasses parametrize the item type (``SequentialQueueWorker[FooItem]``)
    and MUST override:

    * :meth:`_stale_reimport_message` — the pre-loop staleness gate, resolving
      ``stale_dict_reimport_error`` in the *subclass* module so per-module test
      patches keep intercepting it.
    * :meth:`_run_item` — mine one item, emitting ``item_started`` /
      ``item_finished`` (and any per-item lifecycle the subclass owns). Return
      ``True`` to abort ``run()`` early *without* emitting ``queue_finished``
      (only YouTube's mid-fetch cancel uses this); ``False`` otherwise.
    """

    # Per-item index; emitted once before that item is mined.
    item_started = pyqtSignal(int)
    # (idx, label, pct). pct == -1 signals indeterminate progress.
    item_progress = pyqtSignal(int, str, int)
    # (idx, result|None, error|None, attempts). Fires exactly once per item
    # that runs to completion (success or terminal failure).
    item_finished = pyqtSignal(int, object, object, int)
    # Fires once after the last item, unless a subclass _run_item returned early.
    queue_finished = pyqtSignal()

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[ItemT],
        curation_callback: Callable[[list], list | None] | None,
        parent=None,
        *,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ) -> None:
        """Initialize the queue worker.

        Args:
            processor: Episode processor instance, or None when
                ``processor_factory`` is provided (built at run() start).
            config: Frozen app config.
            items: Queue items to process, in order (frozen snapshot).
            curation_callback: Forwarded to the processor. Pass ``None`` to
                disable entirely (the tab gates on its review checkbox).
            parent: Optional parent QObject.
            processor_factory: Zero-arg callable that returns an EpisodeProcessor.
                Mutually exclusive with a non-None ``processor``. When supplied,
                the processor is constructed on the worker thread inside run(),
                keeping the GUI thread free of the registry/sqlite/CSV work.
        """
        self._validate_processor_xor_factory(processor, processor_factory)
        super().__init__(parent)
        self._processor = processor
        self._processor_factory = processor_factory
        self._config = config
        self._items = items
        self._curation_callback = curation_callback
        # Skip channel: items the user removed mid-run (Clear / row [x]).
        # The run loop iterates the frozen constructor snapshot, so a GUI-side
        # removal alone would still mine the item — cards for rows that no
        # longer exist. Identity-based membership (queue items are eq=False);
        # ``self._items`` keeps every snapshot item alive, so identities are
        # stable for the whole run.
        self._skip_lock = threading.Lock()
        self._skipped: set[ItemT] = set()

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor shared by every queue item.

        None before run() has built it via a supplied ``processor_factory``;
        the GUI caches it back after the run so subsequent runs reuse it.
        """
        return self._processor

    def skip_item(self, item: ItemT) -> None:
        """Mark *item* to be skipped if its turn has not started yet.

        Thread-safe; called from the GUI thread when the user removes a queued
        row during an active run. Best-effort: an item the loop has already
        started runs to completion (its idx signals resolve against the tab's
        frozen run-items snapshot, which tolerates removed rows). Skipped items
        emit no signals at all.
        """
        with self._skip_lock:
            self._skipped.add(item)

    def _is_skipped(self, item: ItemT) -> bool:
        """Thread-safe membership check for the skip channel."""
        with self._skip_lock:
            return item in self._skipped

    def run(self) -> None:
        """Process the queue end-to-end.

        Template method: the pre-loop staleness gate, deferred factory build,
        and the cancel/skip loop scaffolding live here; the subclass
        :meth:`_run_item` supplies the per-item body.
        """
        try:
            self._run_queue()
        except Exception as exc:  # noqa: BLE001 - QThread.run exception boundary
            logger.exception("%s run failed", type(self).__name__)
            self.error.emit(f"{type(exc).__name__}: {exc}")
            self.queue_finished.emit()

    def _run_queue(self) -> None:
        """Run queue logic inside :meth:`run`'s exception boundary."""
        # Schema-staleness pre-loop gate: abort the whole queue once with a
        # single actionable error when an enabled indexed dict slot needs
        # reimport — before any mining — instead of one silent zero-card
        # failure row per queued item.
        stale_msg = self._stale_reimport_message()
        if stale_msg is not None:
            self.error.emit(stale_msg)
            self.queue_finished.emit()
            return
        # Build the processor on the worker thread when a factory was supplied,
        # keeping the GUI thread free of the slow registry/sqlite/CSV work during
        # EpisodeProcessor construction. A factory failure ends the whole run:
        # emit error, then queue_finished so the tab recovers like any exit path.
        if self._processor is None:
            assert self._processor_factory is not None  # validated in __init__
            try:
                self._processor = self._processor_factory()
            except Exception as exc:  # noqa: BLE001 - surface every failure to GUI
                logger.exception("%s processor build failed", type(self).__name__)
                self.error.emit(f"{type(exc).__name__}: {exc}")
                self.queue_finished.emit()
                return
        for idx, item in enumerate(self._items):
            if self.is_cancelled:
                break
            if self._is_skipped(item):
                continue  # removed from the GUI mid-run; no signals for it
            if self._run_item(idx, item):
                # Subclass requested an early return (YouTube mid-fetch cancel)
                # that suppresses queue_finished entirely.
                return
        self.queue_finished.emit()

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _stale_reimport_message(self) -> str | None:
        """Return the schema-staleness abort message, or None to proceed.

        Overridden per subclass to call that module's ``stale_dict_reimport_error``
        so ``patch("...<subclass>_queue_worker.stale_dict_reimport_error")`` keeps
        intercepting the check.
        """
        raise NotImplementedError

    def _run_item(self, idx: int, item: ItemT) -> bool:
        """Mine one item; emit ``item_started`` / ``item_finished``.

        Return ``True`` to abort ``run()`` without emitting ``queue_finished``
        (YouTube's mid-fetch cancellation); ``False`` to continue the queue.
        """
        raise NotImplementedError
