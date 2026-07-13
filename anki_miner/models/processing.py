"""Data models for processing results and validation."""

from dataclasses import dataclass, field
from enum import Enum

#: Exact ``errors`` entry a cancelled run carries (see
#: ``EpisodeProcessor._make_cancelled_result``). The queue-result classifier
#: keys the CANCELLED verdict on this marker, so it is the single source of
#: truth — the orchestrator imports it rather than re-spelling the literal.
CANCELLED_ERROR = "Processing cancelled by user"


class MiningOutcome(Enum):
    """Terminal classification of a non-raising ``process_*`` return.

    The queue workers/tabs get a ``ProcessingResult`` back whether the run
    succeeded, failed, or was Stopped mid-mine (none of these raise). Mapping
    the result to this enum lets every queue site route it identically:
    SUCCESS → COMPLETED, CANCELLED → re-minable (READY), FAILED → ERROR.
    """

    SUCCESS = "success"
    CANCELLED = "cancelled"
    FAILED = "failed"


def classify_result(result: object | None) -> MiningOutcome:
    """Classify a non-raising ``process_*`` return into a :class:`MiningOutcome`.

    * ``None`` or any non-empty ``errors`` that includes :data:`CANCELLED_ERROR`
      → :attr:`MiningOutcome.CANCELLED` (a Stop mid-mine — re-minable).
    * Any other non-empty ``errors`` (or a missing result) → FAILED.
    * Empty ``errors`` → SUCCESS.

    ``errors`` is only honoured when it is a genuine ``list``. Bare
    ``MagicMock``/``SimpleNamespace`` stand-ins (whose auto-generated ``errors``
    attribute is a truthy Mock, not a list) therefore classify as SUCCESS —
    matching the historical behaviour of the queue sites, which keyed success
    solely on the worker's ``error is None`` and never inspected ``errors``.
    """
    if result is None:
        return MiningOutcome.FAILED
    errors = getattr(result, "errors", None)
    if not isinstance(errors, list) or not errors:
        return MiningOutcome.SUCCESS
    if CANCELLED_ERROR in errors:
        return MiningOutcome.CANCELLED
    return MiningOutcome.FAILED


def result_error_text(result: object | None, default: str = "Mining failed") -> str:
    """Join a result's ``errors`` into a display string, or ``default``.

    Used by the queue sites to surface a FAILED result's reason when the worker
    passed no explicit error string (the return-based failure path).
    """
    errors = getattr(result, "errors", None)
    if isinstance(errors, list) and errors:
        return "; ".join(str(e) for e in errors)
    return default


@dataclass
class ProcessingResult:
    """Result of processing an episode or folder."""

    total_words_found: int
    new_words_found: int
    cards_created: int
    errors: list[str] = field(default_factory=list)
    elapsed_time: float = 0.0
    comprehension_percentage: float = 0.0  # Percentage of words already known
    card_ids: list[int] = field(default_factory=list)
    video_file: str = ""
    subtitle_file: str = ""
    mined_forms: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Check if processing was successful (no critical errors)."""
        return len(self.errors) == 0

    def __str__(self) -> str:
        return (
            f"ProcessingResult(total={self.total_words_found}, "
            f"new={self.new_words_found}, created={self.cards_created}, "
            f"time={self.elapsed_time:.1f}s)"
        )


@dataclass
class ValidationIssue:
    """A single validation issue."""

    component: str  # Component that failed (e.g., "AnkiConnect", "ffmpeg")
    severity: str  # "ERROR" or "WARNING"
    message: str  # Description of the issue

    def __str__(self) -> str:
        return f"[{self.severity}] {self.component}: {self.message}"


@dataclass
class ValidationResult:
    """Result of system validation."""

    ankiconnect_ok: bool
    ffmpeg_ok: bool
    deck_exists: bool
    note_type_exists: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    ffprobe_ok: bool = True

    @property
    def all_passed(self) -> bool:
        """Check if all validation checks passed."""
        return all(
            [
                self.ankiconnect_ok,
                self.ffmpeg_ok,
                self.ffprobe_ok,
                self.deck_exists,
                self.note_type_exists,
            ]
        )

    def get_errors(self) -> list[ValidationIssue]:
        """Get all error-level issues."""
        return [issue for issue in self.issues if issue.severity == "ERROR"]

    def get_warnings(self) -> list[ValidationIssue]:
        """Get all warning-level issues."""
        return [issue for issue in self.issues if issue.severity == "WARNING"]

    def __str__(self) -> str:
        status = "PASSED" if self.all_passed else "FAILED"
        error_count = len(self.get_errors())
        warning_count = len(self.get_warnings())
        return f"ValidationResult({status}, errors={error_count}, warnings={warning_count})"
