"""Null presenter for testing (no output)."""

from anki_miner.models import (
    ProcessingResult,
    TokenizedWord,
    ValidationResult,
)


class NullPresenter:
    """Present output to nowhere (testing implementation)."""

    def show_info(self, message: str) -> None:
        """Display an informational message (no-op)."""
        pass

    def show_success(self, message: str) -> None:
        """Display a success message (no-op)."""
        pass

    def show_warning(self, message: str) -> None:
        """Display a warning message (no-op)."""
        pass

    def show_error(self, message: str) -> None:
        """Display an error message (no-op)."""
        pass

    def show_validation_result(self, result: ValidationResult) -> None:
        """Display the result of system validation (no-op)."""
        pass

    def show_processing_result(self, result: ProcessingResult) -> None:
        """Display the result of processing an episode (no-op)."""
        pass

    def show_word_preview(self, words: list[TokenizedWord]) -> None:
        """Display a preview of discovered words (no-op)."""
        pass


class NullProgressCallback:
    """Null implementation of progress callback (testing)."""

    def on_start(self, total: int, description: str) -> None:
        """Called when an operation starts (no-op)."""
        pass

    def on_progress(self, current: int, item_description: str) -> None:
        """Called when an item is processed (no-op)."""
        pass

    def on_complete(self) -> None:
        """Called when an operation completes (no-op)."""
        pass

    def on_error(self, item_description: str, error_message: str) -> None:
        """Called when an item fails (no-op)."""
        pass
