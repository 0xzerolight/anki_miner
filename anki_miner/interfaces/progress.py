"""Progress callback protocol for progress reporting."""

from collections.abc import Callable
from typing import Protocol

# Shared alias for the (bytes_done, bytes_total, description) download progress
# callback used by the resource downloader and its installer wrappers.
DownloadProgressFn = Callable[[int, int, str], None]


class ProgressCallback(Protocol):
    """Interface for progress reporting during long-running operations.

    This protocol allows services to report progress without knowing
    how it will be displayed (CLI progress bar, GUI progress bar, etc).
    """

    def on_stage(self, index: int, total: int, name: str) -> None:
        """Called when the pipeline enters one of its numbered stages.

        This is the only whole-run position the pipeline actually knows. It
        replaced the hard-coded stage weights that used to blend every stage
        into a single invented percentage.

        Args:
            index: 1-based position of this stage
            total: How many stages this pipeline has
            name: The stage's own name, e.g. ``Extracting media``
        """
        ...

    def on_start(self, total: int, description: str) -> None:
        """Called when an operation starts.

        Args:
            total: Total number of items to process
            description: Description of the operation
        """
        ...

    def on_progress(self, current: int, item_description: str) -> None:
        """Called when an item is processed.

        Args:
            current: Current item number (1-based)
            item_description: Description of the current item
        """
        ...

    def on_complete(self) -> None:
        """Called when an operation completes."""
        ...

    def on_error(self, item_description: str, error_message: str) -> None:
        """Called when an item fails.

        Args:
            item_description: Description of the failed item
            error_message: Error message
        """
        ...
