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


class AnkiWriteState(Enum):
    """What a run can PROVE about whether Anki notes were written (D30).

    Automatic retry re-runs the whole pipeline, so it is only ever safe when
    the app can prove no note reached the collection — replaying a run that
    already created notes duplicates the user's cards.

    The three answers are deliberately asymmetric:

    * :attr:`NO_NOTE_WRITE` — no ``addNotes`` request was ever in flight. The
      ONLY state an automatic retry may act on.
    * :attr:`NOTE_WRITE_UNCERTAIN` — an ``addNotes`` request was in flight and
      no validated response came back. A dropped connection is indistinguishable
      from a successful write whose reply was lost, so this is the fail-closed
      answer for every ambiguity, not just proven-lost responses.
    * :attr:`NOTE_WRITE_CONFIRMED` — AnkiConnect returned at least one non-null
      note id, so notes definitely exist.

    UNCERTAIN and CONFIRMED both block automatic retry; they are kept apart
    because only CONFIRMED can also name the created ids.
    """

    NO_NOTE_WRITE = "no_note_write"
    NOTE_WRITE_UNCERTAIN = "note_write_uncertain"
    NOTE_WRITE_CONFIRMED = "note_write_confirmed"


class TerminalOutcome(Enum):
    """Whole-run terminal outcome shared by workers and tabs."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


def classify_terminal_outcome(
    succeeded: int,
    failed: int,
    *,
    skipped: int = 0,
    cancelled: bool = False,
    fatal: bool = False,
) -> TerminalOutcome:
    """Classify whole-run counts with cancel/fatal precedence.

    ``skipped`` items (output exists, overwrite off) are neutral: they never
    make a run a success on their own count, but they keep a run with failures
    at PARTIAL rather than FAILED — a deliberate skip is not a failed item.
    """
    if cancelled:
        return TerminalOutcome.CANCELLED
    if fatal:
        return TerminalOutcome.FAILED
    if failed:
        return TerminalOutcome.PARTIAL if (succeeded or skipped) else TerminalOutcome.FAILED
    return TerminalOutcome.SUCCESS


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
    #: Anki note-write provenance for this run (D30). Stamped by
    #: ``EpisodeProcessor._run_pipeline`` on every result it returns. The
    #: default is the FAIL-CLOSED answer: a result nobody stamped has made no
    #: proof, so it must not be treated as safe to replay.
    anki_write_state: AnkiWriteState = AnkiWriteState.NOTE_WRITE_UNCERTAIN
    #: True only when the failure came from a source-proven transient cause (a
    #: connection drop or timeout), which a later attempt may well survive. A
    #: deterministic failure re-run fails identically, so it stays False.
    failure_is_transient: bool = False

    @property
    def success(self) -> bool:
        """Check if processing was successful (no critical errors)."""
        return len(self.errors) == 0

    @property
    def auto_retry_eligible(self) -> bool:
        """Whether this run may be re-run automatically without asking the user.

        Both halves are required and both are checked by identity, never by
        truthiness: a ``MagicMock`` stand-in or the bare ``"no_note_write"``
        string must not unlock a retry that could duplicate cards.
        """
        return self.failure_is_transient is True and self.anki_write_state is AnkiWriteState.NO_NOTE_WRITE

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
    field_mapping_ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    ffprobe_ok: bool = True
    #: Per-tool success text ("<version> [tier]") for tools that passed, keyed by
    #: tool name. ``issues`` carries only failures, so without this the version and
    #: resolution tier a check already computed were thrown away — and a UI wanting
    #: to show them had to re-run a `--version` subprocess, on the GUI thread.
    #: Defaulted so existing constructions keep working.
    tool_versions: dict[str, str] = field(default_factory=dict)

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
                self.field_mapping_ok,
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
