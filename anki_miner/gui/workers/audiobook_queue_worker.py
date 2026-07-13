"""Queue worker that mines multiple audiobook file pairs sequentially.

Drives a list of :class:`AudiobookQueueItem` through mining one at a time.
Unlike the YouTube queue worker there is no fetch/probe stage, no retry
(retry-once existed only for network fetch errors), and no workspace
allocation: ``process_episode`` owns its own temp folder for local files.

Signal shapes, ctor validation, the skip channel, ``curation_processor``, and
the stale-gate + factory-build ``run()`` preamble all live on
:class:`SequentialQueueWorker`; this subclass supplies only the per-item body.

* ``item_started(int)`` — idx fired before the item is mined. Items removed
  mid-run via :meth:`skip_item` are silently skipped.
* ``item_progress(int, str, int)`` — idx, label, pct.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Attempts is always 1 (no retry). Fires
  exactly once per item that runs.
* ``queue_finished()`` — fires once at the bottom of ``run()``. There is no
  early-return suppression path here: YouTube suppresses it only on mid-fetch
  cancellation, and there is no fetch stage. A cancel mid-mine propagates via
  the worker's ``_cancel_event``, handed to ``process_episode`` as
  ``cancel_event``: the processor's next phase checkpoint returns a cancelled
  ``ProcessingResult`` (no exception), ``item_finished`` fires for that item,
  and the loop-top check then stops the queue, with ``queue_finished`` still
  emitted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.dictionary.registry import stale_dict_reimport_error

logger = logging.getLogger(__name__)


class AudiobookQueueWorker(SequentialQueueWorker[AudiobookQueueItem]):
    """Worker thread that mines a queue of audiobook file pairs sequentially.

    Each item runs through ``EpisodeProcessor.process_episode`` with
    ``audio_only=True``. Any exception ends that item with the error string;
    the queue continues on to the next item regardless of per-item outcome.
    """

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[AudiobookQueueItem],
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
        # Published for the GUI curation bridge. Attribute names mirror the
        # other queue workers' _curation_* so the shared curation bridge can
        # read the same attribute names regardless of which worker is driving
        # it. Set per item before mining starts (the worker blocks in the
        # curation wait, so reads from the GUI thread are race-free).
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = config.subtitle_offset

    def _stale_reimport_message(self) -> str | None:
        return stale_dict_reimport_error(self._config)

    def _run_item(self, idx: int, item: AudiobookQueueItem) -> bool:
        """Mine one item, emitting item_started + item_finished. Never aborts early."""
        self.item_started.emit(idx)
        try:
            result = self._mine_one(idx, item)
        except Exception as exc:  # noqa: BLE001 - surface any failure to GUI
            logger.exception("AudiobookQueueWorker item %d failed", idx)
            self.item_finished.emit(idx, None, f"{type(exc).__name__}: {exc}", 1)
        else:
            self.item_finished.emit(idx, result, None, 1)
        return False

    def _mine_one(self, idx: int, item: AudiobookQueueItem) -> object:
        """Mine a single audiobook file pair.

        Returns the orchestrator ``ProcessingResult`` on success; any
        exception propagates to the error handling in ``_run_item``.
        """
        # Publish curation media context BEFORE mining so the GUI curation
        # bridge can read it while the worker blocks in the curation wait.
        # The invariant is "publish the offset mining will apply": subtitle
        # parsing applies config.subtitle_offset unconditionally, local files
        # included, so the bridge must see the same value.
        self._curation_video = item.audio_file
        self._curation_subtitle = item.subtitle_file
        self._curation_offset = self._config.subtitle_offset

        mining_cb = QueueMiningProgressAdapter(idx, self.item_progress.emit)

        assert self._processor is not None  # built at run() start
        return self._processor.process_episode(
            item.audio_file,
            item.subtitle_file,
            audio_only=True,
            progress_callback=mining_cb,
            curation_callback=self._curation_callback,
            episode_name_override=item.audio_file.stem,
            series_name_override="Audio",
            # Bridge Stop mid-mine into the processor's phase checkpoints.
            # Must be the event, NOT processor.cancel(): the sticky
            # _cancelled flag poisons the shared processor across runs.
            cancel_event=self._cancel_event,
        )
