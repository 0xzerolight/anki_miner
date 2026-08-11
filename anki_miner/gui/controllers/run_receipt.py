"""What a finished mining run knows about itself (D20, D23).

A twenty-item queue used to end in twenty modal dialogs, and the run's own
numbers lived in the progress bar -- which cancel and failure reset, throwing
away the counts at exactly the moment they became interesting. This module is
where those numbers live instead: one accumulator per run, fed by the same
per-item signals the screen already handles, outliving the bar and the worker.

Three rules are enforced here rather than at each of the eight mining screens:

* **A cancelled run still did work.** Items already finished stay counted, so
  the run can say what it managed before it stopped.
* **They are notes, not cards.** ``AnkiService`` returns *note* ids and
  ``ProcessingResult.card_ids`` holds them; the receipt names them correctly.
* **Elapsed is active time** (:func:`~anki_miner.gui.utils.progress_telemetry.active_duration`),
  so a run that spanned a laptop sleep does not claim to have worked through it.

The accumulator owns no timer and no Qt object: it is handed timestamps. All
wording lives in the view, so this stays a pure model.
"""

from __future__ import annotations

from dataclasses import dataclass

from anki_miner.gui.utils.progress_telemetry import ActiveDuration, active_duration
from anki_miner.models.processing import (
    MiningOutcome,
    ProcessingResult,
    TerminalOutcome,
    classify_result,
    classify_terminal_outcome,
)


@dataclass
class _RunAggregateResult(ProcessingResult):
    """A details result that keeps the whole run's terminal outcome."""

    terminal_outcome: TerminalOutcome = TerminalOutcome.SUCCESS


@dataclass(frozen=True)
class RunReceipt:
    """The immutable record of one finished run. Rendered, never recomputed."""

    outcome: TerminalOutcome
    #: Items the run set out to do, frozen at launch.
    items_total: int
    items_completed: int
    items_failed: int
    notes_added: int
    #: Confirmed Anki note ids, in the order the run created them.
    note_ids: tuple[int, ...]
    duration: ActiveDuration
    #: Per-item results, when the screen's worker produced any. The Batch queue
    #: worker reports counts only, so this is legitimately empty there.
    results: tuple[ProcessingResult, ...] = ()

    @property
    def has_details(self) -> bool:
        """Whether there is anything for **View details** to open."""
        return bool(self.results)

    def aggregate_result(self) -> ProcessingResult | None:
        """Fold every item into one result for the details/undo surface.

        ``ResultsDialog`` shows one run, so a twelve-episode run is presented as
        one aggregate rather than twelve dialogs. Undo then owns the whole run's
        note ids, which is the granularity a user who just watched a queue
        finish actually wants.
        """
        if not self.results:
            return None
        errors: list[str] = []
        mined_forms: list[str] = []
        note_ids: list[int] = []
        for result in self.results:
            errors.extend(result.errors)
            mined_forms.extend(result.mined_forms)
            note_ids.extend(result.card_ids)
        known = sum(r.total_words_found - r.new_words_found for r in self.results)
        total_words = sum(r.total_words_found for r in self.results)
        return _RunAggregateResult(
            total_words_found=total_words,
            new_words_found=sum(r.new_words_found for r in self.results),
            cards_created=self.notes_added,
            errors=errors,
            elapsed_time=self.duration.active_s,
            comprehension_percentage=(known / total_words * 100) if total_words else 0.0,
            card_ids=note_ids,
            mined_forms=mined_forms,
            terminal_outcome=self.outcome,
        )


class RunReceiptAccumulator:
    """Collects one run's outcome as its items finish.

    Constructed at run start with the two clocks, then fed by whichever per-item
    signal the owning screen already handles. It is deliberately tolerant about
    what an item *is*: some workers hand back a
    :class:`~anki_miner.models.processing.ProcessingResult`, the Batch queue
    worker hands back counts, and both have to end up in the same receipt.
    """

    def __init__(self, items_total: int, *, monotonic_start: float, wall_start: float) -> None:
        """Begin accumulating a run of ``items_total`` items.

        Args:
            items_total: Items handed to the worker, frozen at launch.
            monotonic_start: ``time.monotonic()`` at launch.
            wall_start: ``time.time()`` at launch.
        """
        self.items_total = items_total
        self.monotonic_start = monotonic_start
        self.wall_start = wall_start
        self._completed = 0
        self._failed = 0
        self._notes = 0
        self._note_ids: list[int] = []
        self._results: list[ProcessingResult] = []
        self._cancel_requested = False
        self._fatal = False

    def record_result(self, result: object | None, error: object = None) -> None:
        """Record one item from the ``(result, error)`` pair a worker emits.

        Classification is
        :func:`~anki_miner.models.processing.classify_result`, so the receipt
        agrees with the queue row and the log about what happened: a non-None
        ``error`` is a worker exception, and a cancelled item carries the
        cancellation marker inside an otherwise ordinary result.
        """
        outcome = MiningOutcome.FAILED if error is not None else classify_result(result)
        processing_result = result if isinstance(result, ProcessingResult) else None
        if processing_result is not None:
            self._results.append(processing_result)
            self._notes += int(processing_result.cards_created or 0)
            if isinstance(processing_result.card_ids, list):
                self._note_ids.extend(int(i) for i in processing_result.card_ids)
        if outcome is MiningOutcome.SUCCESS:
            self._completed += 1
            if processing_result is None:
                self._notes += int(getattr(result, "cards_created", 0) or 0)
                ids = getattr(result, "card_ids", None)
                if isinstance(ids, list):
                    self._note_ids.extend(int(i) for i in ids)
        elif outcome is MiningOutcome.FAILED:
            self._failed += 1
        # A cancelled item is neither: it did not finish and it did not fail.

    def record_counts(self, *, notes_added: int, failed: bool) -> None:
        """Record one item that produced counts but no result object.

        The Batch queue worker owns its own item lifecycle and emits
        ``item_completed(id, cards_created)`` /
        ``item_failed(id, message, cards_created)``, so this is the only shape
        available there.
        """
        self._notes += max(0, notes_added)
        if failed:
            self._failed += 1
            return
        self._completed += 1

    def mark_cancel_requested(self) -> None:
        """Note that the user asked to stop. Outranks every other outcome."""
        self._cancel_requested = True

    def mark_failed(self) -> None:
        """Note a run-level fatal (a preflight refusal, a worker exception)."""
        self._fatal = True

    def finish(self, *, monotonic_now: float, wall_now: float) -> RunReceipt:
        """Seal the run and return its receipt.

        Args:
            monotonic_now: ``time.monotonic()`` at the terminal moment.
            wall_now: ``time.time()`` at the terminal moment.
        """
        return RunReceipt(
            outcome=classify_terminal_outcome(
                self._completed,
                self._failed,
                cancelled=self._cancel_requested,
                fatal=self._fatal,
            ),
            items_total=self.items_total,
            items_completed=self._completed,
            items_failed=self._failed,
            notes_added=self._notes,
            note_ids=tuple(self._note_ids),
            duration=active_duration(
                monotonic_start=self.monotonic_start,
                monotonic_now=monotonic_now,
                wall_start=self.wall_start,
                wall_now=wall_now,
            ),
            results=tuple(self._results),
        )
