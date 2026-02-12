"""Data models for processing results and validation."""

from dataclasses import dataclass, field


@dataclass
class ProcessingResult:
    """Result of processing an episode or folder."""

    total_words_found: int
    new_words_found: int
    cards_created: int
    errors: list[str] = field(default_factory=list)
    elapsed_time: float = 0.0

    @property
    def success(self) -> bool:
        """Check if processing was successful (no critical errors)."""
        return len(self.errors) == 0

    @property
    def has_new_words(self) -> bool:
        """Check if new words were found."""
        return self.new_words_found > 0

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

    @property
    def all_passed(self) -> bool:
        """Check if all validation checks passed."""
        return all(
            [
                self.ankiconnect_ok,
                self.ffmpeg_ok,
                self.deck_exists,
                self.note_type_exists,
            ]
        )

    @property
    def has_errors(self) -> bool:
        """Check if there are any error-level issues."""
        return any(issue.severity == "ERROR" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        """Check if there are any warning-level issues."""
        return any(issue.severity == "WARNING" for issue in self.issues)

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
