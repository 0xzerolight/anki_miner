"""Queue worker that mines multiple reading sources sequentially.

Drives a list of :class:`ReadingQueueItem` (manga volumes / novel files)
through mining one at a time. Structurally a mirror of
:class:`AudiobookQueueWorker`: no fetch/probe stage and no workspace
allocation, sharing the base's bounded automatic retry (D30-B). The one
structural addition is the per-item *load* step — a
reading source is a ref that must be resolved to a :class:`ReadingDocument`
via ``detector.load`` before mining. A load failure (DRM, invalid source,
parse error) ends only that item; the queue continues.

Unlike the audiobook worker, this worker owns the queue-item lifecycle: it
sets ``status``/``cards_created``/``error_message`` on each item as it runs
(READY → PROCESSING → COMPLETED/ERROR). The GUI reads item state in the queued
signal handlers, which run after the worker has already recorded it.

Signal shapes, ctor validation, the skip channel, ``curation_processor``, and
the stale-gate + factory-build ``run()`` preamble all live on
:class:`SequentialQueueWorker`; this subclass supplies only the per-item body.

* ``item_started(int)`` — idx, fired before the item is mined.
* ``item_progress(int, str)`` — idx, label.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Fires exactly once per item that runs.
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

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.gui.workers._queue_worker_base import AttemptOutcome, SequentialQueueWorker
from anki_miner.models import CANCELLED_ERROR, MiningOutcome, ProcessingResult, classify_result, result_error_text
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingDocument
from anki_miner.models.reading_queue import ReadingQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.reading import detector
from anki_miner.services.resource_staleness import stale_resource_reimport_error

logger = logging.getLogger(__name__)


class ReadingQueueWorker(SequentialQueueWorker[ReadingQueueItem]):
    """Worker thread that mines a queue of reading sources sequentially.

    Each item is resolved with ``detector.load`` then run through
    ``EpisodeProcessor.process_reading``. Any load or mining failure ends
    that item with the error string; the queue continues to the next item
    regardless of per-item outcome.
    """

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
        """Initialize the queue worker (see :class:`SequentialQueueWorker`)."""
        super().__init__(
            processor,
            config,
            items,
            curation_callback,
            parent,
            processor_factory=processor_factory,
        )
        # D8 (amended): no _curation_video/_curation_subtitle/_curation_offset
        # here — novels curation is table-only. Manga curation reads
        # curation_document instead (set per item in _mine_one). It is read
        # only by the GUI-side _build_curation_context while this worker is
        # parked in the curation Event wait, so the parked worker cannot
        # overwrite it mid-dialog and no lock is needed.
        self.curation_document: ReadingDocument | None = None

    def _stale_reimport_message(self) -> str | None:
        return stale_resource_reimport_error(self._config)

    def _run_item(self, idx: int, item: ReadingQueueItem) -> bool:
        """Load + mine one item, owning its READY→PROCESSING→COMPLETED/ERROR
        lifecycle. Never aborts the queue early (returns False)."""
        self.item_started.emit(idx)
        outcome, attempts = self._attempt_cycle(idx, lambda: self._attempt_once(idx, item))
        if outcome.error is not None:
            self._fail_item(idx, item, outcome.error, attempts)
        else:
            self._record_result(idx, item, outcome.result, attempts)
        return False

    def _attempt_once(self, idx: int, item: ReadingQueueItem) -> AttemptOutcome:
        """Run one load + mine attempt, classifying whether it may be repeated."""
        try:
            return self._classify_return(self._mine_one(idx, item))
        except OperationCancelled:
            logger.info("ReadingQueueWorker item %d load cancelled", idx)
            return self._classify_return(
                ProcessingResult(
                    total_words_found=0,
                    new_words_found=0,
                    cards_created=0,
                    errors=[CANCELLED_ERROR],
                )
            )
        except SetupError as exc:
            # The load step and process_reading raise SetupError with a
            # crafted, user-facing message (DRM, invalid source, note-type
            # misconfig); surface it verbatim rather than type-prefixed.
            logger.warning("ReadingQueueWorker item %d setup error: %s", idx, exc)
            return self._classify_exception(exc, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - surface any failure to GUI
            logger.exception("ReadingQueueWorker item %d failed", idx)
            return self._classify_exception(exc)

    def _mark_item_claimed(self, item: ReadingQueueItem) -> None:
        item.status = ReadyItemStatus.PROCESSING

    def _record_result(self, idx: int, item: ReadingQueueItem, result: object, attempts: int = 1) -> None:
        """Route a non-raising ``process_reading`` return by its outcome.

        ``process_reading`` never raises on a failed or Stopped-mid-mine run; it
        returns a ``ProcessingResult`` whose ``errors`` is populated. Marking any
        such return COMPLETED (the old behaviour) hid failures behind a green
        "Mined 0 cards" and stranded a cancelled item as un-re-minable. Classify
        instead: SUCCESS → COMPLETED, CANCELLED → back to READY (re-minable),
        FAILED → ERROR (keeping any partial ``cards_created``).
        """
        cards = int(getattr(result, "cards_created", 0) or 0)
        outcome = classify_result(result)
        if outcome is MiningOutcome.SUCCESS:
            item.status = ReadyItemStatus.COMPLETED
            item.cards_created = cards
            item.error_message = None
            self.item_finished.emit(idx, result, None, attempts)
        elif outcome is MiningOutcome.CANCELLED:
            item.status = ReadyItemStatus.READY
            item.cards_created = cards
            item.error_message = None
            self.item_finished.emit(idx, result, None, attempts)
        else:
            message = result_error_text(result)
            item.status = ReadyItemStatus.ERROR
            item.cards_created = cards
            item.error_message = message
            self.item_finished.emit(idx, None, message, attempts)

    def _fail_item(self, idx: int, item: ReadingQueueItem, message: str, attempts: int = 1) -> None:
        """Record a per-item failure on the item and via ``item_finished``."""
        item.status = ReadyItemStatus.ERROR
        item.error_message = message
        self.item_finished.emit(idx, None, message, attempts)

    def _mine_one(self, idx: int, item: ReadingQueueItem) -> object:
        """Load and mine a single reading source.

        Resolves the source ref to a document (``detector.load``), then mines
        it. Returns the orchestrator ``ProcessingResult`` on success; any
        exception (load or mining) propagates to the error handling in
        ``_run_item``.
        """
        document = detector.load(item.source, cancel_check=self.check_cancelled)
        # Published for the manga tab's curation context (page images). Set
        # before process_reading so it is always the in-flight item's document
        # by the time the curation callback parks this thread.
        self.curation_document = document

        mining_cb = QueueMiningProgressAdapter(idx, self.item_progress.emit)

        assert self._processor is not None  # built at run() start
        return self._processor.process_reading(
            document,
            progress_callback=mining_cb,
            curation_callback=self._active_curation_callback,
            # Bridge Stop mid-mine into the processor's phase checkpoints.
            # Must be the event, NOT processor.cancel(): the sticky
            # _cancelled flag poisons the shared processor across runs.
            cancel_event=self._cancel_event,
        )
