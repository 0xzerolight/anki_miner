"""Progress callback protocol for progress reporting."""

from typing import Protocol


class ProgressCallback(Protocol):
    """Interface for progress reporting during long-running operations.

    This protocol allows services to report progress without knowing
    how it will be displayed (CLI progress bar, GUI progress bar, etc).
    """

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
