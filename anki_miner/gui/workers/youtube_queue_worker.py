"""Queue worker that fetches + mines multiple YouTube videos sequentially.

Drives a list of :class:`YouTubeQueueItem` through fetch + mine one at a
time. Each item gets retry-once semantics on :class:`YouTubeFetchError`
(but not on other exceptions). Each attempt allocates its own workspace
under ``config.media_temp_folder / "youtube" / run-<hex>`` and removes it
in a ``finally`` block; the next attempt starts from a clean directory.

This worker is the SOLE OWNER of each workspace directory: the fetcher and
orchestrator only write into it, they never create or delete it. Because
cleanup happens in the per-attempt ``finally``, a failed first attempt does
not leak its workspace into the retry. On cancel, the fetcher kills the
yt-dlp process tree (including the ffmpeg child) via psutil BEFORE the
rmtree fires, so cleanup never races a live writer.

Signal shapes (exact):

* ``item_started(int)`` — idx fired before the first attempt for the item.
  Items removed mid-run via :meth:`YouTubeQueueWorker.skip_item` are
  silently skipped: no ``item_started`` / ``item_finished`` for them.
* ``item_progress(int, str, int)`` — idx, label, pct. ``pct`` is an
  ``int(round(0..100))`` percentage, or ``-1`` for indeterminate phases.
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Fires exactly once per item that
  completes (cancel during retry path returns early instead).
* ``queue_finished()`` — fires once at the bottom of ``run()`` unless the
  worker returned early due to mid-fetch cancellation.

Cancel semantics deliberately mirror the spec:

* Before each item: outer ``if self.is_cancelled: break`` exits the for
  loop; ``queue_finished`` still emits.
* Inside the ``YouTubeFetchError`` except: re-check ``is_cancelled`` and
  ``return`` immediately so no further signals fire. The fetcher's psutil
  subprocess-kill path raises ``YouTubeFetchError("Cancelled")`` when the
  cancel event fires mid-download — retrying that would just kill the
  freshly-spawned subprocess again.
* Mid-mine: ``cancel_event`` is forwarded to ``process_youtube_url``, which
  bridges it into ``process_episode``'s phase checkpoints for that run. A
  Stop landing after the fetch therefore returns a cancelled
  ``ProcessingResult`` (no exception): ``item_finished`` fires for that item
  and the loop-top check then stops the queue.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import YouTubeFetchError
from anki_miner.gui.workers._queue_progress import (
    QueueMiningProgressAdapter as _QueueMiningProgressAdapter,
)
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.models.youtube import FetchedMedia
from anki_miner.models.youtube_queue import YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.dictionary.registry import stale_dict_reimport_error

logger = logging.getLogger(__name__)


class YouTubeQueueWorker(ProcessorOwningWorker):
    """Worker thread that processes a queue of YouTube URLs sequentially.

    Each item runs fetch + mine through ``EpisodeProcessor.process_youtube_url``.
    A :class:`YouTubeFetchError` triggers exactly one retry against a fresh
    workspace; any other exception ends that item with the error string.
    The queue continues on to the next item regardless of per-item outcome,
    except on mid-fetch cancellation, which returns from ``run()`` early.
    """

    # Per-item index; emitted once before the first attempt for that item.
    item_started = pyqtSignal(int)
    # (idx, label, pct) where pct == -1 signals indeterminate progress.
    item_progress = pyqtSignal(int, str, int)
    # (idx, result|None, error|None, attempts). Fires exactly once per item
    # that runs to completion (success or terminal failure).
    item_finished = pyqtSignal(int, object, object, int)
    # Fires once after the last item, unless mid-fetch cancellation
    # returned from run() early.
    queue_finished = pyqtSignal()

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[YouTubeQueueItem],
        curation_callback: Callable[[list], list | None] | None,
        preview_mode: bool,
        parent=None,
        *,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ) -> None:
        """Initialize the queue worker.

        Args:
            processor: Episode processor instance, or None when
                ``processor_factory`` is provided (built at run() start).
            config: Frozen app config; ``media_temp_folder`` is the workspace root.
            items: Queue items to process, in order. Each must already have
                ``video_id`` and ``resolved_sub_mode`` populated (probe step
                handles that before items reach this worker).
            curation_callback: Forwarded unconditionally to
                ``process_youtube_url``; ``process_episode`` internally gates
                invocation on ``not preview_mode``, so curation fires only on
                Mine runs. Pass ``None`` to disable entirely.
            preview_mode: If True, mining produces previews instead of cards.
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
        self._preview_mode = preview_mode
        # Published for the GUI curation bridge. Attribute names mirror
        # BatchQueueWorkerThread's _curation_* so the shared curation bridge can
        # read the same attribute names regardless of which worker is driving it.
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = config.subtitle_offset
        # Skip channel: items the user removed mid-run (Clear / row [x]).
        # The run loop iterates the frozen constructor snapshot, so a GUI-side
        # removal alone would still mine the item — cards for rows that no
        # longer exist. Identity-based membership (YouTubeQueueItem is
        # eq=False); ``self._items`` keeps every snapshot item alive, so
        # identities are stable for the whole run.
        self._skip_lock = threading.Lock()
        self._skipped: set[YouTubeQueueItem] = set()

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor shared by every queue item.

        None before run() has built it via a supplied ``processor_factory``;
        the GUI caches it back after the run so subsequent runs reuse it.
        """
        return self._processor

    def skip_item(self, item: YouTubeQueueItem) -> None:
        """Mark *item* to be skipped if its turn has not started yet.

        Thread-safe; called from the GUI thread when the user removes a queued
        row during an active run. Best-effort: an item the loop has already
        started runs to completion (its idx signals resolve against the tab's
        frozen ``_run_items`` snapshot, which tolerates removed rows). Skipped
        items emit no signals at all.
        """
        with self._skip_lock:
            self._skipped.add(item)

    def _is_skipped(self, item: YouTubeQueueItem) -> bool:
        """Thread-safe membership check for the skip channel."""
        with self._skip_lock:
            return item in self._skipped

    def run(self) -> None:
        """Process the queue end-to-end with retry-once per fetch error."""
        # Schema-staleness pre-loop gate (4.0): abort the whole queue once with
        # a single actionable error when an enabled indexed dict slot needs
        # reimport — before any fetch/mine — instead of one silent zero-card
        # failure row per queued video.
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
                logger.exception("YouTubeQueueWorker processor build failed")
                self.error.emit(f"{type(exc).__name__}: {exc}")
                self.queue_finished.emit()
                return
        for idx, item in enumerate(self._items):
            if self.is_cancelled:
                break
            if self._is_skipped(item):
                continue  # removed from the GUI mid-run; no signals for it
            self.item_started.emit(idx)
            attempts = 0
            last_error: str | None = None
            result: object = None
            for attempt in (0, 1):
                attempts = attempt + 1
                # Allocate inside the try: an mkdir OSError (ENOSPC, perms)
                # must be a per-item error caught below, not propagate out of
                # run() and strand the whole queue with the item stuck in
                # PROCESSING (no item_finished / queue_finished). The finally
                # skips cleanup when allocation never produced a directory.
                workspace: Path | None = None
                try:
                    workspace = self._allocate_workspace()
                    result = self._mine_one(idx, item, workspace)
                    last_error = None
                    break
                except YouTubeFetchError as exc:
                    if self.is_cancelled:
                        # Mid-fetch cancellation: don't retry, don't emit
                        # item_finished, and skip queue_finished entirely.
                        return
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt == 0:
                        continue  # retry once
                except Exception as exc:  # noqa: BLE001 - surface any other failure to GUI
                    logger.exception("YouTubeQueueWorker item failed")
                    last_error = f"{type(exc).__name__}: {exc}"
                    break
                finally:
                    if workspace is not None:
                        shutil.rmtree(workspace, ignore_errors=True)
            if last_error is None:
                self.item_finished.emit(idx, result, None, attempts)
            else:
                self.item_finished.emit(idx, None, last_error, attempts)
        self.queue_finished.emit()

    def _allocate_workspace(self) -> Path:
        """Create and return a fresh per-attempt workspace directory.

        The intermediate ``youtube`` directory is created with mode 0o700 and
        the leaf workspace is allocated via ``tempfile.mkdtemp`` (also 0o700),
        mirroring ``episode_processor._allocate_run_temp_folder``.  Explicit
        modes are used rather than relying on the process umask so
        cookie-authenticated files never land world-readable (OVH-062).
        """
        youtube_dir = self._config.media_temp_folder / "youtube"
        youtube_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Enforce 0o700 even if the directory already exists with a looser mode
        # (e.g. created by an older version of the app).
        youtube_dir.chmod(0o700)
        workspace = Path(tempfile.mkdtemp(prefix="run-", dir=youtube_dir))
        return workspace

    def _mine_one(self, idx: int, item: YouTubeQueueItem, workspace: Path) -> object:
        """Run a single fetch + mine attempt against ``workspace``.

        Returns the orchestrator ``ProcessingResult`` on success; any
        exception propagates to the retry/error handling in ``run``.

        Items that reach this method are READY (probe already populated
        ``video_id``, ``resolved_sub_mode``, and ``video_info``). The guard
        below narrows Optional types from the queue model for mypy and raises
        explicitly rather than silently passing None into yt-dlp.
        """
        if item.video_id is None or item.resolved_sub_mode is None or item.video_info is None:
            raise RuntimeError(
                f"READY item {item.url!r} missing video_id, resolved_sub_mode, or video_info — probe step incomplete"
            )

        mining_cb = _QueueMiningProgressAdapter(idx, self.item_progress.emit)

        assert self._processor is not None  # built at run() start
        return self._processor.process_youtube_url(
            url=item.url,
            video_id=item.video_id,
            workspace=workspace,
            sub_mode=item.resolved_sub_mode,
            cancel_event=self._cancel_event,
            progress_callback=mining_cb,
            fetch_progress_cb=lambda label, frac: self._emit_fetch_progress(idx, label, frac),
            # process_episode gates curation on not preview_mode, so this fires
            # only on Mine runs; passing it unconditionally is correct.
            curation_callback=self._curation_callback,
            on_fetched=self._capture_curation_media,
            preview_mode=self._preview_mode,
            source_label=item.video_info.title,
        )

    def _capture_curation_media(self, fetched: FetchedMedia) -> None:
        """Record download paths so the GUI can build a curation media context.

        Runs on the worker thread, before curation, so the GUI can read the
        paths via ``_curation_video`` / ``_curation_subtitle`` from its slot.
        """
        self._curation_video = fetched.video_file
        self._curation_subtitle = fetched.subtitle_file

    def _emit_fetch_progress(self, idx: int, label: str, frac: float | None) -> None:
        """Translate fetcher progress into the ``item_progress`` signal.

        ``frac`` is a float in [0.0, 1.0] for determinate progress, or
        ``None`` for indeterminate stages (e.g. "Merging"). Out-of-range
        floats are clamped defensively; yt-dlp occasionally emits tail
        values >1.0.
        """
        if frac is None:
            pct = -1
        else:
            clamped = max(0.0, min(1.0, frac))
            pct = int(round(clamped * 100))
        self.item_progress.emit(idx, label, pct)
