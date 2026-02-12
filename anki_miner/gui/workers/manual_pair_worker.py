"""Worker thread for processing manually-paired video/subtitle files."""

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.presenters import GUIProgressCallback
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.orchestration import EpisodeProcessor


class ManualPairWorkerThread(CancellableWorker):
    """Worker thread for processing pre-paired video/subtitle files.

    Inherits thread-safe cancellation from CancellableWorker.
    """

    result_ready = pyqtSignal(list)  # List[ProcessingResult]

    def __init__(
        self,
        episode_processor: EpisodeProcessor,
        pairs,  # List[FilePair]
        progress_callback: GUIProgressCallback | None = None,
        parent=None,
    ):
        """Initialize the manual pair worker thread.

        Args:
            episode_processor: Episode processor for handling each pair
            pairs: List of FilePair objects to process
            progress_callback: Optional progress callback
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.episode_processor = episode_processor
        self.pairs = pairs
        self.progress_callback = progress_callback

    def run(self):
        """Process all pairs sequentially in background thread."""
        try:
            if self.check_cancelled():
                return

            results = []

            # Report overall progress
            if self.progress_callback:
                self.progress_callback.on_start(
                    len(self.pairs), f"Processing {len(self.pairs)} episodes"
                )

            for i, pair in enumerate(self.pairs, 1):
                if self.check_cancelled():
                    break

                # Process this pair
                try:
                    result = self.episode_processor.process_episode(
                        pair.video,
                        pair.subtitle,
                        preview_mode=False,
                        progress_callback=None,  # Don't nest progress callbacks
                    )
                    results.append(result)

                    # Report progress
                    if self.progress_callback:
                        self.progress_callback.on_progress(
                            i,
                            f"[{i}/{len(self.pairs)}] {pair.video.name}: {result.cards_created} cards",
                        )

                except Exception as e:
                    # Report error for this pair but continue
                    if self.progress_callback:
                        self.progress_callback.on_error(pair.video.name, str(e))

            # Report completion
            if self.progress_callback and not self.check_cancelled():
                self.progress_callback.on_complete()

            if not self.check_cancelled():
                self.result_ready.emit(results)

        except Exception as e:
            if not self.check_cancelled():
                self.error.emit(str(e))
