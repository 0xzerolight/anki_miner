"""Worker thread for processing manually-paired video/subtitle files."""

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.models.processing import ProcessingResult
from anki_miner.orchestration import EpisodeProcessor

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
        if episode_processor is not None and processor_factory is not None:
            raise ValueError("Provide either episode_processor or processor_factory, not both")
        if episode_processor is None and processor_factory is None:
            raise ValueError("Either episode_processor or processor_factory must be provided")
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
        """Process all pairs sequentially in background thread."""
        try:
            if self.check_cancelled():
                return

            # Build the processor on the worker thread when a factory was
            # supplied, keeping the GUI thread free of the slow
            # registry/sqlite/CSV work during EpisodeProcessor construction.
            if self.episode_processor is None:
                assert self._processor_factory is not None  # validated in __init__
                self.episode_processor = self._processor_factory()

            results = []

            # Report overall (pair-level) progress on the dedicated signals so
            # the tab's single overall bar can compose pair counts with the
            # per-episode stage sweep from progress_callback below.
            self.batch_started.emit(len(self.pairs))

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
                        preview_mode=False,
                        progress_callback=self.progress_callback,
                        curation_callback=self.curation_callback,
                    )
                    results.append(result)

                except Exception as e:
                    # Report error for this pair but continue.
                    # Append a soft-failure result so the batch summary counts
                    # it as failed (mirrors BatchQueueWorkerThread behaviour).
                    logger.exception("ManualPairWorkerThread pair %s failed", pair.video.name)
                    results.append(
                        ProcessingResult(
                            total_words_found=0,
                            new_words_found=0,
                            cards_created=0,
                            errors=[str(e)],
                            video_file=str(pair.video),
                            subtitle_file=str(pair.subtitle),
                        )
                    )
                    if self.progress_callback:
                        self.progress_callback.on_error(pair.video.name, str(e))

                # Advance the Overall Progress bar after each pair regardless of
                # success/failure, so it stays monotonic when a pair errors.
                self.pair_finished.emit(i, len(self.pairs))

            # Report completion
            if self.progress_callback and not self.check_cancelled():
                self.progress_callback.on_complete()

            if not self.check_cancelled():
                self.result_ready.emit(results)

        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("ManualPairWorkerThread unhandled exception")
            if not self.check_cancelled():
                self.error.emit(str(e))
