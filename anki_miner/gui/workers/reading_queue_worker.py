"""Queue worker that mines multiple reading sources sequentially.

Drives a list of :class:`ReadingQueueItem` (manga volumes / novel files)
through mining one at a time. Structurally a mirror of
:class:`AudiobookQueueWorker`: no fetch/probe stage, no retry, no workspace
allocation. The one structural addition is the per-item *load* step — a
reading source is a ref that must be resolved to a :class:`ReadingDocument`
via ``detector.load`` before mining. A load failure (DRM, invalid source,
parse error) ends only that item; the queue continues.

Unlike the audiobook worker, this worker owns the queue-item lifecycle: it
sets ``status``/``cards_created``/``error_message`` on each item as it runs
(READY → PROCESSING → COMPLETED/ERROR). The GUI reads item state in the queued
signal handlers, which run after the worker has already recorded it.

Signal shapes mirror the audiobook worker so the tab code mirrors too:

* ``item_started(int)`` — idx, fired before the item is mined.
* ``item_progress(int, str, int)`` — idx, label, pct.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Attempts is always 1 (no retry). Fires
  exactly once per item that runs.
* ``queue_finished()`` — fires once at the bottom of ``run()``. A cancel
  mid-mine propagates via the worker's ``_cancel_event`` (handed to
  ``process_reading`` as ``cancel_event``): the processor's next checkpoint
  returns a cancelled ``ProcessingResult`` (no exception), ``item_finished``
  fires for that item, and the loop-top check then stops the queue with
  ``queue_finished`` still emitted.

D8 (amended): this worker publishes NO ``_curation_video``/``_curation_subtitle``/
``_curation_offset`` attributes — novels curation is table-only. For manga it
publishes :attr:`curation_document` (the in-flight item's loaded
``ReadingDocument``) so the manga tab can build a page-image curation context.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.models.reading_queue import ReadingItemStatus, ReadingQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.dictionary.registry import stale_dict_reimport_error
from anki_miner.services.reading import detector
from anki_miner.services.reading.models import ReadingDocument

logger = logging.getLogger(__name__)


class ReadingQueueWorker(ProcessorOwningWorker):
    """Worker thread that mines a queue of reading sources sequentially.

    Each item is resolved with ``detector.load`` then run through
    ``EpisodeProcessor.process_reading``. Any load or mining failure ends
    that item with the error string; the queue continues to the next item
    regardless of per-item outcome.
    """

    # Per-item index; emitted once before that item is mined.
    item_started = pyqtSignal(int)
    # (idx, label, pct).
    item_progress = pyqtSignal(int, str, int)
    # (idx, result|None, error|None, attempts). Attempts is always 1.
    item_finished = pyqtSignal(int, object, object, int)
    # Fires once after the last item.
    queue_finished = pyqtSignal()

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[ReadingQueueItem],
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
            curation_callback: Forwarded to ``process_reading``. Pass ``None``
                to disable entirely (the tab gates on its review checkbox).
            parent: Optional parent QObject.
            processor_factory: Zero-arg callable that returns an EpisodeProcessor.
                Mutually exclusive with a non-None ``processor``.  When supplied,
                the processor is constructed on the worker thread inside run(),
                keeping the GUI thread free of the registry/sqlite/CSV work.
        """
        if processor is not None and processor_factory is not None:
            raise ValueError("Provide either processor or processor_factory, not both")
        if processor is None and processor_factory is None:
            raise ValueError("Either processor or processor_factory must be provided")
        super().__init__(parent)
        self._processor = processor
        self._processor_factory = processor_factory
        self._config = config
        self._items = items
        self._curation_callback = curation_callback
        # D8 (amended): no _curation_video/_curation_subtitle/_curation_offset
        # here — novels curation is table-only. Manga curation reads
        # curation_document instead (set per item in _mine_one). It is read
        # only by the GUI-side _build_curation_context while this worker is
        # parked in the curation Event wait, so the parked worker cannot
        # overwrite it mid-dialog and no lock is needed.
        self.curation_document: ReadingDocument | None = None

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor shared by every queue item.

        None before run() has built it via a supplied ``processor_factory``;
        the GUI caches it back after the run so subsequent runs reuse it.
        """
        return self._processor

    def run(self) -> None:
        """Process the queue end-to-end, one mining attempt per item."""
        # Schema-staleness pre-loop gate: abort the whole queue once with a
        # single actionable error when an enabled indexed dict slot needs
        # reimport, instead of one silent zero-card failure row per item.
        stale_msg = stale_dict_reimport_error(self._config)
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
                logger.exception("ReadingQueueWorker processor build failed")
                self.error.emit(f"{type(exc).__name__}: {exc}")
                self.queue_finished.emit()
                return
        for idx, item in enumerate(self._items):
            # Cancellation is checked between items, which also runs before the
            # load of the current item (load is the first step of _mine_one).
            if self.is_cancelled:
                break
            item.status = ReadingItemStatus.PROCESSING
            self.item_started.emit(idx)
            try:
                result = self._mine_one(idx, item)
            except SetupError as exc:
                # The load step and process_reading raise SetupError with a
                # crafted, user-facing message (DRM, invalid source, note-type
                # misconfig); surface it verbatim rather than type-prefixed.
                logger.warning("ReadingQueueWorker item %d setup error: %s", idx, exc)
                self._fail_item(idx, item, str(exc))
            except Exception as exc:  # noqa: BLE001 - surface any failure to GUI
                logger.exception("ReadingQueueWorker item %d failed", idx)
                self._fail_item(idx, item, f"{type(exc).__name__}: {exc}")
            else:
                cards = int(getattr(result, "cards_created", 0) or 0)
                item.status = ReadingItemStatus.COMPLETED
                item.cards_created = cards
                item.error_message = None
                self.item_finished.emit(idx, result, None, 1)
        self.queue_finished.emit()

    def _fail_item(self, idx: int, item: ReadingQueueItem, message: str) -> None:
        """Record a per-item failure on the item and via ``item_finished``."""
        item.status = ReadingItemStatus.ERROR
        item.error_message = message
        self.item_finished.emit(idx, None, message, 1)

    def _mine_one(self, idx: int, item: ReadingQueueItem) -> object:
        """Load and mine a single reading source.

        Resolves the source ref to a document (``detector.load``), then mines
        it. Returns the orchestrator ``ProcessingResult`` on success; any
        exception (load or mining) propagates to the error handling in ``run``.
        """
        document = detector.load(item.source)
        # Published for the manga tab's curation context (page images). Set
        # before process_reading so it is always the in-flight item's document
        # by the time the curation callback parks this thread.
        self.curation_document = document

        mining_cb = QueueMiningProgressAdapter(idx, self.item_progress.emit)

        assert self._processor is not None  # built at run() start
        return self._processor.process_reading(
            document,
            progress_callback=mining_cb,
            curation_callback=self._curation_callback,
            # Bridge Stop mid-mine into the processor's phase checkpoints.
            # Must be the event, NOT processor.cancel(): the sticky
            # _cancelled flag poisons the shared processor across runs.
            cancel_event=self._cancel_event,
        )
