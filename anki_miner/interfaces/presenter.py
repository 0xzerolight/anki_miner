"""Presenter protocol for output abstraction."""

from typing import Protocol

from anki_miner.models import (
    ProcessingResult,
    TokenizedWord,
    ValidationResult,
)


class PresenterProtocol(Protocol):
    """Interface for presenting output to the user.

    This protocol abstracts all output operations so the same business
    logic can run against different presentation layers. Two concrete
    implementations exist: ``GUIPresenter`` (emits Qt signals to the
    PyQt6 main window) and ``NullPresenter`` (silent, used in tests).
    """

    def show_info(self, message: str) -> None:
        """Display an informational message.

        Args:
            message: The informational message to display
        """
        ...

    def show_success(self, message: str) -> None:
        """Display a success message.

        Args:
            message: The success message to display
        """
        ...

    def show_warning(self, message: str) -> None:
        """Display a warning message.

        Args:
            message: The warning message to display
        """
        ...

    def show_error(self, message: str) -> None:
        """Display an error message.

        Args:
            message: The error message to display
        """
        ...

    def show_validation_result(self, result: ValidationResult) -> None:
        """Display the result of system validation.

        Args:
            result: The validation result to display
        """
        ...

    def show_processing_result(self, result: ProcessingResult) -> None:
        """Display the result of processing an episode.

        Args:
            result: The processing result to display
        """
        ...

    def show_word_preview(self, words: list[TokenizedWord]) -> None:
        """Display a preview of discovered words.

        Args:
            words: List of words to preview
        """
        ...
