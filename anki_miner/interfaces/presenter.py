"""Presenter protocol for output abstraction."""

from typing import Protocol, runtime_checkable

from anki_miner.models import (
    ProcessingResult,
    ValidationResult,
)


@runtime_checkable
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

    def show_stage(self, index: int, total: int, name: str) -> None:
        """Announce which pipeline stage the run has reached.

        The presenter is where stage identity is *said*. It is deliberately
        separate from the per-run progress callback, because the four Reading
        sub-tabs share one presenter: the presenter writes a line, the callback
        updates one run's state.

        Args:
            index: 1-based position of this stage.
            total: How many stages this pipeline has.
            name: The stage's own name, e.g. ``Extracting media``.
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

    def show_run_details(self, result: ProcessingResult) -> None:
        """Open the full details of a finished run, because the user asked.

        Distinct from :meth:`show_processing_result`, which every item reports
        and which must never interrupt: this one is raised by **View details**
        on a run receipt, so it is allowed to open the detail surface.

        Args:
            result: The whole run, aggregated into one result.
        """
        ...
