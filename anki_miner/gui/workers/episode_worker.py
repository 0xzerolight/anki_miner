"""Worker thread for episode processing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.orchestration import EpisodeProcessor


class EpisodeWorkerThread(ProcessorOwningWorker):
    """Worker thread for processing episodes in background.

    This thread runs the episode processing workflow in the background to keep
    the GUI responsive. It emits signals when finished or when an error occurs.

    Inherits thread-safe cancellation from CancellableWorker.
    """

    result_ready = pyqtSignal(object)  # ProcessingResult

    def __init__(
        self,
        processor: EpisodeProcessor,
        video_file: Path,
        subtitle_file: Path,
        preview_mode: bool,
        progress_callback: ProgressCallback,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
        *,
        audio_track_override: int | None = None,
    ):
        """Initialize the episode worker thread.

        Args:
            processor: Episode processor instance
            video_file: Path to video file
            subtitle_file: Path to subtitle file
            preview_mode: If True, only preview words without creating cards
            progress_callback: Progress callback for updates
            curation_callback: Optional callback for word curation
            parent: Optional parent QObject
            audio_track_override: If set, forces the given audio_index instead of
                auto-detecting the Japanese track. None means auto-detect.
        """
        super().__init__(parent)
        self.processor = processor
        self.video_file = video_file
        self.subtitle_file = subtitle_file
        self.preview_mode = preview_mode
        self.progress_callback = progress_callback
        self.curation_callback = curation_callback
        self.audio_track_override = audio_track_override

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The constructor-supplied processor (typed contract for GUI readers)."""
        return self.processor

    def cancel(self) -> None:
        """Cancel processing, propagating to the processor."""
        super().cancel()
        self.processor.cancel()

    def run(self) -> None:
        """Execute episode processing in background thread."""
        try:
            if self.check_cancelled():
                return

            result = self.processor.process_episode(
                self.video_file,
                self.subtitle_file,
                self.preview_mode,
                self.progress_callback,
                curation_callback=self.curation_callback,
                audio_track_override=self.audio_track_override,
            )

            if not self.check_cancelled():
                self.result_ready.emit(result)

        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            if not self.check_cancelled():
                error_msg = f"Error processing episode: {str(e)}"
                self.error.emit(error_msg)
