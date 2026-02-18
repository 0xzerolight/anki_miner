"""Worker thread for processing batch queue of multiple folder pairs."""

from dataclasses import replace

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.models.batch_queue import BatchQueue


class BatchQueueWorkerThread(CancellableWorker):
    """Worker thread for processing multiple folder pairs sequentially.

    Inherits thread-safe cancellation from CancellableWorker.
    """

    # Signals for queue-level progress
    queue_started = pyqtSignal(int)  # total_items
    item_started = pyqtSignal(str, str)  # item_id, display_name
    item_completed = pyqtSignal(str, int)  # item_id, cards_created
    item_failed = pyqtSignal(str, str)  # item_id, error_message
    queue_finished = pyqtSignal(int)  # total_cards_created

    def __init__(
        self,
        batch_queue: BatchQueue,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback | None = None,
        stats_service=None,
        parent=None,
    ):
        """Initialize the batch queue worker thread.

        Args:
            batch_queue: BatchQueue containing items to process
            config: Application configuration (will be modified per-item for offset)
            presenter: GUI presenter for output
            progress_callback: Optional progress callback for updates
            stats_service: Optional statistics recording service
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self.batch_queue = batch_queue
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service

    def run(self):
        """Process all pending items in queue sequentially."""
        total_cards = 0
        total_items = self.batch_queue.pending_count

        self.queue_started.emit(total_items)

        while not self.check_cancelled():
            item = self.batch_queue.get_next_pending()
            if item is None:
                break  # No more pending items

            # Signal processing start (status updated by GUI thread via signal handler)
            self.item_started.emit(item.id, item.display_name)

            try:
                # Create config with item's subtitle offset
                config_with_offset = replace(self.config, subtitle_offset=item.subtitle_offset)

                # Create processor for this item with its specific offset
                episode_processor = create_episode_processor(
                    config_with_offset, self.presenter, self.stats_service
                )

                # Use FilePairMatcher for cross-folder pairing
                from anki_miner.utils.file_pairing import FilePairMatcher

                pairs = FilePairMatcher.find_pairs_by_episode_number(
                    item.anime_folder, item.subtitle_folder
                )

                if not pairs:
                    raise ValueError("No matching video/subtitle pairs found")

                # Process each pair using episode processor
                cards_for_item = 0
                for pair in pairs:
                    if self.check_cancelled():
                        break

                    result = episode_processor.process_episode(
                        pair.video,
                        pair.subtitle,
                        preview_mode=False,
                        progress_callback=self.progress_callback,
                    )
                    cards_for_item += result.cards_created

                # Signal completion (status updated by GUI thread via signal handler)
                total_cards += cards_for_item
                self.item_completed.emit(item.id, cards_for_item)

            except Exception as e:
                self.item_failed.emit(item.id, str(e))

        self.queue_finished.emit(total_cards)
