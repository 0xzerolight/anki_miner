"""Worker thread for episode processing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.orchestration import EpisodeProcessor


class EpisodeWorkerThread(CancellableWorker):
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
        progress_callback: GUIProgressCallback,
        curation_callback: Callable[[list], list] | None = None,
        parent=None,
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
        """
        super().__init__(parent)
        self.processor = processor
        self.video_file = video_file
        self.subtitle_file = subtitle_file
        self.preview_mode = preview_mode
        self.progress_callback = progress_callback
        self.curation_callback = curation_callback

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
            )

            if not self.check_cancelled():
                self.result_ready.emit(result)

        except Exception as e:
            if not self.check_cancelled():
                error_msg = f"Error processing episode: {str(e)}"
                self.error.emit(error_msg)
