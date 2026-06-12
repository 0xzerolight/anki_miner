"""Queue worker that mines multiple audiobook file pairs sequentially.

Drives a list of :class:`AudiobookQueueItem` through mining one at a time.
Unlike the YouTube queue worker there is no fetch/probe stage, no retry
(retry-once existed only for network fetch errors), and no workspace
allocation: ``process_episode`` owns its own temp folder for local files.

Signal shapes (exact, mirroring :class:`YouTubeQueueWorker` so the tab code
mirrors too):

* ``item_started(int)`` — idx fired before the item is mined. Items removed
  mid-run via :meth:`AudiobookQueueWorker.skip_item` are silently skipped:
  no ``item_started`` / ``item_finished`` for them.
* ``item_progress(int, str, int)`` — idx, label, pct.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Attempts is always 1 (no retry). Fires
  exactly once per item that runs.
* ``queue_finished()`` — fires once at the bottom of ``run()``. There is no
  early-return suppression path here: YouTube suppresses it only on
  mid-fetch cancellation, and there is no fetch stage. A cancel mid-mine
  surfaces as a cancelled ``ProcessingResult`` (no exception):
  ``item_finished`` fires for that item and the loop-top check then stops
  the queue, with ``queue_finished`` still emitted.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.models.audiobook_queue import AudiobookQueueItem
from anki_miner.orchestration import EpisodeProcessor


class AudiobookQueueWorker(ProcessorOwningWorker):
    """Worker thread that mines a queue of audiobook file pairs sequentially.

    Each item runs through ``EpisodeProcessor.process_episode`` with
    ``audio_only=True``. Any exception ends that item with the error string;
    the queue continues on to the next item regardless of per-item outcome.
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
        processor: EpisodeProcessor,
        config: AnkiMinerConfig,
        items: list[AudiobookQueueItem],
        curation_callback: Callable[[list], list | None] | None,
        preview_mode: bool,
        parent=None,
    ) -> None:
        """Initialize the queue worker.

        Args:
            processor: Episode processor instance.
            config: Frozen app config.
            items: Queue items to process, in order (frozen snapshot).
            curation_callback: Forwarded unconditionally to
                ``process_episode``, which internally gates invocation on
                ``not preview_mode``, so curation fires only on Mine runs.
                Pass ``None`` to disable entirely.
            preview_mode: If True, mining produces previews instead of cards.
            parent: Optional parent QObject.
        """
        super().__init__(parent)
        self._processor = processor
        self._config = config
        self._items = items
        self._curation_callback = curation_callback
        self._preview_mode = preview_mode
        # Published for the GUI curation bridge. Attribute names mirror
        # the other queue workers' _curation_* so the shared curation bridge
        # can read the same attribute names regardless of which worker is
        # driving it. Set per item before mining starts (the worker blocks in
        # the curation wait, so reads from the GUI thread are race-free).
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = 0.0
        # Skip channel: items the user removed mid-run (Clear / row [x]).
        # The run loop iterates the frozen constructor snapshot, so a GUI-side
        # removal alone would still mine the item — cards for rows that no
        # longer exist. Identity-based membership (AudiobookQueueItem is
        # eq=False); ``self._items`` keeps every snapshot item alive, so
        # identities are stable for the whole run.
        self._skip_lock = threading.Lock()
        self._skipped: set[AudiobookQueueItem] = set()

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The constructor-supplied processor, shared by every queue item."""
        return self._processor

    def skip_item(self, item: AudiobookQueueItem) -> None:
        """Mark *item* to be skipped if its turn has not started yet.

        Thread-safe; called from the GUI thread when the user removes a queued
        row during an active run. Best-effort: an item the loop has already
        started runs to completion (its idx signals resolve against the tab's
        frozen run-items snapshot, which tolerates removed rows). Skipped
        items emit no signals at all.
        """
        with self._skip_lock:
            self._skipped.add(item)

    def _is_skipped(self, item: AudiobookQueueItem) -> bool:
        """Thread-safe membership check for the skip channel."""
        with self._skip_lock:
            return item in self._skipped

    def run(self) -> None:
        """Process the queue end-to-end, one mining attempt per item."""
        for idx, item in enumerate(self._items):
            if self.is_cancelled:
                break
            if self._is_skipped(item):
                continue  # removed from the GUI mid-run; no signals for it
            self.item_started.emit(idx)
            try:
                result = self._mine_one(idx, item)
            except Exception as exc:  # noqa: BLE001 - surface any failure to GUI
                self.item_finished.emit(idx, None, f"{type(exc).__name__}: {exc}", 1)
            else:
                self.item_finished.emit(idx, result, None, 1)
        self.queue_finished.emit()

    def _mine_one(self, idx: int, item: AudiobookQueueItem) -> object:
        """Mine a single audiobook file pair.

        Returns the orchestrator ``ProcessingResult`` on success; any
        exception propagates to the error handling in ``run``.
        """
        # Publish curation media context BEFORE mining so the GUI curation
        # bridge can read it while the worker blocks in the curation wait.
        # Audiobook pairs are local files used as-is, so the offset is 0.
        self._curation_video = item.audio_file
        self._curation_subtitle = item.subtitle_file
        self._curation_offset = 0.0

        mining_cb = QueueMiningProgressAdapter(idx, self.item_progress.emit)

        return self._processor.process_episode(
            item.audio_file,
            item.subtitle_file,
            audio_only=True,
            preview_mode=self._preview_mode,
            progress_callback=mining_cb,
            # process_episode gates curation on not preview_mode, so this fires
            # only on Mine runs; passing it unconditionally is correct.
            curation_callback=self._curation_callback,
            episode_name_override=item.audio_file.stem,
            series_name_override="Audiobook",
        )
