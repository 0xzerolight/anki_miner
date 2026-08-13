"""Worker thread for processing manually-paired video/subtitle files."""

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers._queue_worker_base import queue_preflight_error
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.models.processing import ProcessingResult
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.services.word_pool import (
    CaptureCurationCallback,
    MinePassStats,
    merge_pools,
    split_selection,
)
from anki_miner.utils.file_pairing import FilePair

logger = logging.getLogger(__name__)


class ManualPairWorkerThread(ProcessorOwningWorker):
    """Worker thread for processing pre-paired video/subtitle files.

    Inherits thread-safe cancellation from CancellableWorker.

    Processor construction: either supply a pre-built ``episode_processor``
    directly, or supply a ``processor_factory`` and leave
    ``episode_processor=None``.  When a factory is given the processor is built
    at the START of ``run()`` on the worker thread, so the GUI thread is never
    blocked by the registry scan / sqlite opens / CSV parses that happen during
    construction (mirrors :class:`EpisodeWorkerThread`).  A factory that raises
    surfaces on the existing ``error`` signal.
    """

    result_ready = pyqtSignal(list)  # List[ProcessingResult]
    # Overall (pair-level) progress, mirroring BatchQueueWorkerThread's
    # queue_started/item_completed so the tab's single overall bar advances
    # per pair. Per-episode stage progress flows separately through
    # progress_callback and is composed into the same bar by the tab.
    batch_started = pyqtSignal(int)  # total pairs
    pair_started = pyqtSignal(int, str)  # 1-based index, display name
    pair_finished = pyqtSignal(int, int)  # completed, total

    def __init__(
        self,
        episode_processor: EpisodeProcessor | None,
        pairs,  # List[FilePair]
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
        *,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ):
        """Initialize the manual pair worker thread.

        Args:
            episode_processor: Episode processor for handling each pair, or None
                when ``processor_factory`` is provided (built at run() start).
            pairs: List of FilePair objects to process
            progress_callback: Optional progress callback
            curation_callback: Optional callback invoked per-pair for word curation
            parent: Optional parent QObject
            processor_factory: Zero-arg callable that returns an EpisodeProcessor.
                Mutually exclusive with a non-None ``episode_processor``.  When
                supplied, the processor is constructed on the worker thread
                inside run().
        """
        self._validate_processor_xor_factory(episode_processor, processor_factory, param_name="episode_processor")
        super().__init__(parent)
        self.episode_processor = episode_processor
        self._processor_factory = processor_factory
        self.pairs = pairs
        self.progress_callback = progress_callback
        self.curation_callback = curation_callback
        # Published per-pair so the GUI bridge can build the dialog's media context.
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = 0.0
        # Season mode: episode → (subtitle, offset), published while the worker
        # is parked at the curation gate (same attribute name as
        # BatchQueueWorkerThread so the tab reads one shape for both workers).
        self._curation_media_map: dict[Path, tuple[Path, float]] | None = None

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor for this run (None before run() builds it via factory)."""
        return self.episode_processor

    def cancel(self) -> None:
        """Cancel processing.

        Sets the thread-safe cancel flag. The processor may not exist yet when
        a factory build is still pending, so the ``episode_processor.cancel()``
        propagation is guarded against None. Mirrors :class:`EpisodeWorkerThread`
        in not poisoning a reused processor's sticky ``_cancelled`` flag once it
        does exist — but this worker holds a single processor for the run, so
        propagating cancel to it remains correct here.
        """
        super().cancel()
        if self.episode_processor is not None:
            self.episode_processor.cancel()

    def run(self):
        """Process all pairs sequentially in background thread.

        Every exit path that got past construction emits ``result_ready`` with
        whatever was accumulated, cancellation included. It used to be emitted
        only on an uncancelled run, so cancelling after a pair had already
        written its notes to Anki left the user with no record that those notes
        existed — the screen went quiet while the cards sat in their collection.
        """
        results: list = []
        try:
            # Inside the try on purpose: ``pairs`` is caller-supplied and its
            # __len__ can raise, and an exception out of QThread.run() aborts
            # the process under PyQt6.
            self.log_start("ManualPairWorkerThread", pairs=len(self.pairs))
            if self.check_cancelled():
                self.result_ready.emit(results)
                return

            # Build the processor on the worker thread when a factory was
            # supplied, keeping the GUI thread free of the slow
            # registry/sqlite/CSV work during EpisodeProcessor construction.
            if self.episode_processor is None:
                assert self._processor_factory is not None  # validated in __init__
                self.episode_processor = self._processor_factory()
            if self.check_cancelled():
                self.result_ready.emit(results)
                return

            preflight_error = queue_preflight_error(
                self.episode_processor._preflight_card_target,
                self.episode_processor.check_offline_dictionary,
            )
            if preflight_error is not None:
                self.error.emit(preflight_error)
                return

            # Report overall (pair-level) progress on the dedicated signals so
            # the tab's single overall bar can compose pair counts with the
            # per-episode stage sweep from progress_callback below.
            self.batch_started.emit(len(self.pairs))

            if self.curation_callback is not None:
                # Season mode: one curator for the whole run — pre-pass every
                # pair, review once, then mine the curated subsets.
                results = self._run_season()
            else:
                for i, pair in enumerate(self.pairs, 1):
                    if self.check_cancelled():
                        break

                    self.pair_started.emit(i, pair.video.name)

                    # Process this pair
                    try:
                        # Mirror BatchQueueWorkerThread's _curation_* attrs so the GUI
                        # bridge reads one attribute name across both batch workers.
                        self._curation_video = pair.video
                        self._curation_subtitle = pair.subtitle
                        self._curation_offset = self.episode_processor.config.subtitle_offset
                        # Pass the callback through so per-episode stages (extract ->
                        # definitions -> cards) drive the composed overall bar; the
                        # processor wraps it in a fresh StageWeightedProgress per
                        # episode.
                        result = self.episode_processor.process_episode(
                            pair.video,
                            pair.subtitle,
                            progress_callback=self.progress_callback,
                            curation_callback=self.curation_callback,
                        )
                        results.append(result)

                    except Exception as e:
                        # Report error for this pair but continue.
                        # Append a soft-failure result so the batch summary counts
                        # it as failed (mirrors BatchQueueWorkerThread behaviour).
                        logger.exception("ManualPairWorkerThread pair %s failed", pair.video.name)
                        results.append(self._soft_failure(pair, e))
                        if self.progress_callback:
                            self.progress_callback.on_error(pair.video.name, str(e))

                    # Advance the Overall Progress bar after each pair regardless of
                    # success/failure, so it stays monotonic when a pair errors.
                    self.pair_finished.emit(i, len(self.pairs))

            # Report completion
            if self.progress_callback and not self.check_cancelled():
                self.progress_callback.on_complete()

            # Emitted on the cancelled path too: the pairs already mined created
            # real notes, and the run's receipt has to be able to say so. The
            # tab guards its own completion painting on its cancel flag, so this
            # cannot read as a finished run.
            self.result_ready.emit(results)

        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            self.report_failure(e, context="ManualPairWorkerThread", on_error=self.error.emit)

    def _soft_failure(self, pair, error: Exception) -> ProcessingResult:
        """Per-pair soft-failure result (mirrors BatchQueueWorkerThread)."""
        return ProcessingResult(
            total_words_found=0,
            new_words_found=0,
            cards_created=0,
            errors=[str(error)],
            video_file=str(pair.video),
            subtitle_file=str(pair.subtitle),
        )

    def _run_season(self) -> list:
        """Season mode: one curator for the whole quick-pairs run.

        Pre-pass every pair with a capture callback (words collected, zero
        cards), show the curator ONCE with the merged pool, then mine each
        pair's curated subset. Returns exactly one result per pre-passed pair
        (mined, pre-pass zero-card success, or soft failure) so the receipt's
        counts stay per-pair accurate. A cancel or curator reject returns only
        the pre-pass soft failures accumulated so far — nothing was mined.

        Progress contract: ``pair_started`` fires during both passes (status
        label moves); ``pair_finished`` ticks only in the mine pass, the run's
        actual mining attempts.
        """
        assert self.episode_processor is not None
        assert self.curation_callback is not None
        processor = self.episode_processor
        total = len(self.pairs)

        # --- Pre-pass: collect every pair's reviewable words (no cards). ---
        capture = CaptureCurationCallback()
        records: list[tuple[FilePair, ProcessingResult]] = []  # (pair, pre-pass outcome)

        def _failures() -> list:
            return [result for _pair, result in records if not result.success]

        for i, pair in enumerate(self.pairs, 1):
            if self.check_cancelled():
                return _failures()
            self.pair_started.emit(i, pair.video.name)
            capture.set_episode(pair.video)
            try:
                result = processor.process_episode(
                    pair.video,
                    pair.subtitle,
                    progress_callback=self.progress_callback,
                    curation_callback=capture,
                )
            except Exception as e:  # noqa: BLE001 — per-pair guard, run continues
                logger.exception("ManualPairWorkerThread season pre-pass pair %s failed", pair.video.name)
                result = self._soft_failure(pair, e)
                if self.progress_callback:
                    self.progress_callback.on_error(pair.video.name, str(e))
            if self.check_cancelled():
                return _failures()
            records.append((pair, result))

        # --- One curator for the merged pool. ---
        pool = merge_pools(capture.pools)
        ok_pairs = [pair for pair, result in records if result.success]
        selection: list | None = []
        if pool and ok_pairs:
            offset = processor.config.subtitle_offset
            first = ok_pairs[0]
            self._curation_video = first.video
            self._curation_subtitle = first.subtitle
            self._curation_offset = offset
            self._curation_media_map = {pair.video: (pair.subtitle, offset) for pair in ok_pairs}
            try:
                selection = self.curation_callback(pool)
            finally:
                self._curation_media_map = None
            if selection is None or self.check_cancelled():
                return _failures()

        # --- Mine pass: each pair gets its curated subset. ---
        subsets = split_selection(selection or [])
        strays = subsets.pop(None, None)
        if strays and ok_pairs:
            # Defensive: unstamped words mine from the first pair.
            subsets.setdefault(ok_pairs[0].video, []).extend(strays)

        results: list = []
        original_stats = processor.stats_service
        if original_stats is not None:
            # The pre-pass already recorded one difficulty row per pair; the
            # mine pass must not insert duplicates.
            processor.stats_service = MinePassStats(original_stats)
        try:
            for i, (pair, prepass_result) in enumerate(records, 1):
                if self.check_cancelled():
                    break
                subset = subsets.get(pair.video, []) if prepass_result.success else []
                if not subset:
                    # Pre-pass failure, or nothing selected from this pair —
                    # the pre-pass outcome IS this pair's result.
                    results.append(prepass_result)
                    self.pair_finished.emit(i, total)
                    continue
                self.pair_started.emit(i, pair.video.name)
                try:
                    result = processor.process_episode(
                        pair.video,
                        pair.subtitle,
                        progress_callback=self.progress_callback,
                        # Curated objects pass through verbatim to phases 3-5.
                        curation_callback=lambda words, _subset=subset: _subset,
                    )
                except Exception as e:  # noqa: BLE001 — per-pair guard, run continues
                    logger.exception("ManualPairWorkerThread season mine pair %s failed", pair.video.name)
                    result = self._soft_failure(pair, e)
                    if self.progress_callback:
                        self.progress_callback.on_error(pair.video.name, str(e))
                results.append(result)
                self.pair_finished.emit(i, total)
        finally:
            processor.stats_service = original_stats
        return results
