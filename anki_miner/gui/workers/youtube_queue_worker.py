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

Signal shapes, ctor validation, the skip channel, ``curation_processor``, and
the stale-gate + factory-build ``run()`` preamble all live on
:class:`SequentialQueueWorker`; this subclass supplies only the per-item body.

Signal shapes (exact):

* ``item_started(int)`` — idx fired before the first attempt for the item.
  Items removed mid-run via :meth:`skip_item` are silently skipped: no
  ``item_started`` / ``item_finished`` for them.
* ``item_progress(int, str, int)`` — idx, label, pct. ``pct`` is an
  ``int(round(0..100))`` percentage covering the WHOLE item as one
  continuous sweep — download fills 0-30, mining fills 30-100 — or ``-1``
  for indeterminate phases (the merge step between download and mining).
* ``item_finished(int, object, object, int)`` — idx, result-or-None,
  error-string-or-None, attempts. Fires exactly once per item that
  completes (cancel during retry path returns early instead).
* ``queue_finished()`` — fires once at the bottom of ``run()`` unless the
  worker returned early due to mid-fetch cancellation.

Cancel semantics deliberately mirror the spec:

* Before each item: outer ``if self.is_cancelled: break`` (in the base loop)
  exits the for loop; ``queue_finished`` still emits.
* Inside the ``YouTubeFetchError`` except: re-check ``is_cancelled`` and
  return ``True`` from ``_run_item`` so the base ``run()`` returns immediately
  and no further signals fire. The fetcher's psutil subprocess-kill path
  raises ``YouTubeFetchError("Cancelled")`` when the cancel event fires
  mid-download — retrying that would just kill the freshly-spawned subprocess
  again.
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
from collections.abc import Callable
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import YouTubeFetchError
from anki_miner.gui.workers._queue_progress import (
    QueueMiningProgressAdapter as _QueueMiningProgressAdapter,
)
from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
from anki_miner.models.youtube import FetchedMedia
from anki_miner.models.youtube_queue import YouTubeQueueItem
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.dictionary.registry import stale_dict_reimport_error

logger = logging.getLogger(__name__)


class YouTubeQueueWorker(SequentialQueueWorker[YouTubeQueueItem]):
    """Worker thread that processes a queue of YouTube URLs sequentially.

    Each item runs fetch + mine through ``EpisodeProcessor.process_youtube_url``.
    A :class:`YouTubeFetchError` triggers exactly one retry against a fresh
    workspace; any other exception ends that item with the error string.
    The queue continues on to the next item regardless of per-item outcome,
    except on mid-fetch cancellation, which returns from ``run()`` early.
    """

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        config: AnkiMinerConfig,
        items: list[YouTubeQueueItem],
        curation_callback: Callable[[list], list | None] | None,
        parent=None,
        *,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ) -> None:
        """Initialize the queue worker (see :class:`SequentialQueueWorker`).

        ``config.media_temp_folder`` is the workspace root. Each item must
        already have ``video_id`` and ``resolved_sub_mode`` populated (the probe
        step handles that before items reach this worker).
        """
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
        # read the same attribute names regardless of which worker is driving it.
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = config.subtitle_offset

    def _stale_reimport_message(self) -> str | None:
        return stale_dict_reimport_error(self._config)

    def _run_item(self, idx: int, item: YouTubeQueueItem) -> bool:
        """Fetch + mine one item with retry-once per fetch error.

        Returns ``True`` on mid-fetch cancellation to make the base ``run()``
        return early (suppressing ``queue_finished``); ``False`` otherwise.
        """
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
                    return True
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
        return False

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
        exception propagates to the retry/error handling in ``_run_item``.

        Items that reach this method are READY (probe already populated
        ``video_id``, ``resolved_sub_mode``, and ``video_info``). The guard
        below narrows Optional types from the queue model for mypy and raises
        explicitly rather than silently passing None into yt-dlp.
        """
        if item.video_id is None or item.resolved_sub_mode is None or item.video_info is None:
            raise RuntimeError(
                f"READY item {item.url!r} missing video_id, resolved_sub_mode, or video_info — probe step incomplete"
            )

        # Mining occupies the 30-100 band of the item's percent; the fetch
        # phase fills 0-30 (see _emit_fetch_progress) so one item is a single
        # continuous sweep with no backward jump at the fetch->mine boundary.
        mining_cb = _QueueMiningProgressAdapter(idx, self.item_progress.emit, band=(30, 100))

        assert self._processor is not None  # built at run() start
        return self._processor.process_youtube_url(
            url=item.url,
            video_id=item.video_id,
            workspace=workspace,
            sub_mode=item.resolved_sub_mode,
            cancel_event=self._cancel_event,
            progress_callback=mining_cb,
            fetch_progress_cb=lambda label, frac: self._emit_fetch_progress(idx, label, frac),
            curation_callback=self._curation_callback,
            on_fetched=self._capture_curation_media,
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
        ``None`` for indeterminate stages (e.g. merging). Out-of-range
        floats are clamped defensively; yt-dlp occasionally emits tail
        values >1.0 — unclamped they would overshoot the mining band's
        30% start and produce a backward step at the fetch->mine boundary.

        The fetch maps into the 0-30 band of the item percent (mining takes
        30-100 via the adapter band above) so the whole item reads as one
        continuous sweep.
        """
        if frac is None:
            pct = -1
        else:
            clamped = max(0.0, min(1.0, frac))
            pct = int(round(clamped * 30))
        self.item_progress.emit(idx, label, pct)
