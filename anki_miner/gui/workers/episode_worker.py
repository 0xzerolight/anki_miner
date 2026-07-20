"""Worker thread for episode processing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.orchestration import EpisodeProcessor

logger = logging.getLogger(__name__)


class EpisodeWorkerThread(ProcessorOwningWorker):
    """Worker thread for processing episodes in background.

    This thread runs the episode processing workflow in the background to keep
    the GUI responsive. It emits signals when finished or when an error occurs.

    Inherits thread-safe cancellation from CancellableWorker.

    Processor construction: either supply a pre-built ``processor`` directly, or
    supply a ``processor_factory`` and leave ``processor=None``.  When a factory
    is given the processor is built at the START of ``run()`` on the worker thread,
    so the GUI thread is never blocked by the registry scan / sqlite opens / CSV
    parses that happen during construction (mirrors the DeckBuilder precedent).
    A factory that raises surfaces on the existing ``error`` signal.
    """

    result_ready = pyqtSignal(object)  # ProcessingResult

    def __init__(
        self,
        processor: EpisodeProcessor | None,
        video_file: Path,
        subtitle_file: Path,
        progress_callback: ProgressCallback,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
        *,
        audio_track_override: int | None = None,
        processor_factory: Callable[[], EpisodeProcessor] | None = None,
    ):
        """Initialize the episode worker thread.

        Args:
            processor: Episode processor instance, or None when processor_factory
                is provided (the processor is then built at run() start).
            video_file: Path to video file
            subtitle_file: Path to subtitle file
            progress_callback: Progress callback for updates
            curation_callback: Optional callback for word curation
            parent: Optional parent QObject
            audio_track_override: If set, forces the given audio_index instead of
                auto-detecting the Japanese track. None means auto-detect.
            processor_factory: Zero-arg callable that returns an EpisodeProcessor.
                Mutually exclusive with a non-None ``processor``.  When supplied,
                the processor is constructed on the worker thread inside run().
        """
        self._validate_processor_xor_factory(processor, processor_factory)
        super().__init__(parent)
        self.processor = processor
        self._processor_factory = processor_factory
        self.video_file = video_file
        self.subtitle_file = subtitle_file
        self.progress_callback = progress_callback
        self.curation_callback = curation_callback
        self.audio_track_override = audio_track_override

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The processor for this run (None before run() has built it via factory)."""
        return self.processor

    def cancel(self) -> None:
        """Cancel processing.

        Sets the thread-safe cancel flag. ``run()`` hands this worker's
        ``_cancel_event`` to ``process_episode`` as the per-run external cancel
        source, so the request reaches the processor's phase checkpoints even
        when cancel fires *during* the factory build — i.e. before
        ``self.processor`` exists (the gap that let a cancelled first-mine still
        create cards). We deliberately do NOT call ``self.processor.cancel()``:
        that sets the processor's sticky ``_cancelled`` flag, which would poison
        the next run of a reused processor (see EpisodeProcessor._external_cancel).
        """
        super().cancel()

    def run(self) -> None:
        """Execute episode processing in background thread."""
        try:
            if self.check_cancelled():
                return

            # Build the processor on the worker thread when a factory was supplied.
            # This keeps the GUI thread free of the slow registry/sqlite/CSV work
            # that happens during EpisodeProcessor construction.
            if self.processor is None:
                assert self._processor_factory is not None  # validated in __init__
                self.processor = self._processor_factory()

            result = self.processor.process_episode(
                self.video_file,
                self.subtitle_file,
                progress_callback=self.progress_callback,
                curation_callback=self.curation_callback,
                audio_track_override=self.audio_track_override,
                cancel_event=self._cancel_event,
            )

            # Once Anki has committed cards, the result owns the only exact
            # note-ID receipt the GUI can register for Undo. A cancel that
            # lands after that commit must not discard it. Zero-commit
            # cancelled runs remain silent.
            if result.cards_created or not self.check_cancelled():
                self.result_ready.emit(result)

        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("EpisodeWorkerThread unhandled exception")
            if not self.check_cancelled():
                error_msg = f"Error processing episode: {str(e)}"
                self.error.emit(error_msg)
