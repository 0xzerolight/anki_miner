"""GUI presenter implementation using Qt signals for thread-safe communication."""

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.models import (
    ProcessingResult,
    TokenizedWord,
    ValidationResult,
)


class GUIPresenter(QObject):
    """Thread-safe presenter using Qt signals.

    Implements PresenterProtocol through structural subtyping (duck typing).
    This avoids metaclass conflicts between QObject and Protocol metaclasses.

    This presenter provides thread-safe communication between worker threads
    and the GUI by using Qt signals. Worker threads can call presenter methods
    (show_info, show_success, etc.) which emit Qt signals that are automatically
    queued for the main GUI thread.
    """

    # Signals for thread-safe communication from worker threads to main thread
    info_signal = pyqtSignal(str)
    success_signal = pyqtSignal(str)
    warning_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    validation_result_signal = pyqtSignal(object)  # ValidationResult
    processing_result_signal = pyqtSignal(object)  # ProcessingResult
    word_preview_signal = pyqtSignal(list)  # List[TokenizedWord]

    def __init__(self, parent=None):
        """Initialize the GUI presenter.

        Args:
            parent: Optional parent QObject
        """
        super().__init__(parent)

    def show_info(self, message: str) -> None:
        """Display an informational message.

        Args:
            message: The informational message to display
        """
        self.info_signal.emit(message)

    def show_success(self, message: str) -> None:
        """Display a success message.

        Args:
            message: The success message to display
        """
        self.success_signal.emit(message)

    def show_warning(self, message: str) -> None:
        """Display a warning message.

        Args:
            message: The warning message to display
        """
        self.warning_signal.emit(message)

    def show_error(self, message: str) -> None:
        """Display an error message.

        Args:
            message: The error message to display
        """
        self.error_signal.emit(message)

    def show_validation_result(self, result: ValidationResult) -> None:
        """Display the result of system validation.

        Args:
            result: The validation result to display
        """
        self.validation_result_signal.emit(result)

    def show_processing_result(self, result: ProcessingResult) -> None:
        """Display the result of processing an episode.

        Args:
            result: The processing result to display
        """
        self.processing_result_signal.emit(result)

    def show_word_preview(self, words: list[TokenizedWord]) -> None:
        """Display a preview of discovered words.

        Args:
            words: List of words to preview
        """
        self.word_preview_signal.emit(words)
