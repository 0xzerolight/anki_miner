"""What a finished run is allowed to claim about itself (D20, D23).

The accumulator is the only thing that survives a run, so it -- not the progress
bar, which cancel and failure wipe -- is where the counts come from. Every rule
here exists because the old terminal dialogs got it wrong:

* A cancelled run still did work. It reports how much, not "cancelled" alone.
* ``cards_created`` counts *notes*; the receipt says so.
* Elapsed is active time. A machine that slept for an hour did not work for one.
"""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.gui.controllers.run_receipt import RunReceiptAccumulator
from anki_miner.gui.utils.progress_telemetry import SUSPEND_GAP_S
from anki_miner.models.processing import (
    CANCELLED_ERROR,
    ProcessingResult,
    TerminalOutcome,
    WhitelistCoverage,
)


def _accumulator(total: int = 3, *, start: float = 100.0) -> RunReceiptAccumulator:
    return RunReceiptAccumulator(total, monotonic_start=start, wall_start=start)


def _finish(acc: RunReceiptAccumulator, *, after: float = 60.0, slept: float = 0.0):
    return acc.finish(
        monotonic_now=100.0 + after,
        wall_now=100.0 + after + slept,
    )


def _result(cards: int, *, ids: list[int] | None = None, errors: list[str] | None = None) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 2,
        new_words_found=cards,
        cards_created=cards,
        errors=list(errors or []),
        card_ids=list(ids if ids is not None else range(1, cards + 1)),
    )


class TestCounts:
    def test_a_clean_run_counts_items_and_notes(self):
        acc = _accumulator(2)
        acc.record_result(_result(3, ids=[11, 12, 13]))
        acc.record_result(_result(2, ids=[21, 22]))

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.SUCCESS
        assert receipt.items_total == 2
        assert receipt.items_completed == 2
        assert receipt.items_failed == 0
        assert receipt.notes_added == 5
        assert receipt.note_ids == (11, 12, 13, 21, 22)

    def test_a_successful_duck_typed_result_keeps_its_counts(self):
        acc = _accumulator(1)
        acc.record_result(SimpleNamespace(cards_created=7, card_ids=[31, 32], errors=[]))

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.SUCCESS
        assert receipt.notes_added == 7
        assert receipt.note_ids == (31, 32)

    def test_a_worker_exception_counts_as_a_failed_item(self):
        acc = _accumulator(2)
        acc.record_result(_result(4))
        acc.record_result(None, "ffmpeg exploded")

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.PARTIAL
        assert (receipt.items_completed, receipt.items_failed) == (1, 1)
        assert receipt.notes_added == 4

    def test_a_result_carrying_errors_counts_as_failed(self):
        acc = _accumulator(1)
        acc.record_result(_result(0, errors=["deck missing"]))

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.FAILED
        assert receipt.items_failed == 1

    def test_a_failed_result_keeps_confirmed_note_writes(self):
        acc = _accumulator(1)
        acc.record_result(_result(2, ids=[101, 102], errors=["later addNotes failed"]))

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.FAILED
        assert receipt.notes_added == 2
        assert receipt.note_ids == (101, 102)

        aggregate = receipt.aggregate_result()
        assert aggregate is not None
        assert aggregate.cards_created == 2
        assert aggregate.card_ids == [101, 102]

    def test_a_cancelled_result_keeps_confirmed_note_writes(self):
        acc = _accumulator(1)
        acc.record_result(_result(2, ids=[201, 202], errors=[CANCELLED_ERROR]))
        acc.mark_cancel_requested()

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.CANCELLED
        assert receipt.notes_added == 2
        assert receipt.note_ids == (201, 202)

    def test_a_cancelled_item_is_neither_completed_nor_failed(self):
        acc = _accumulator(3)
        acc.record_result(_result(2, ids=[1, 2]))
        acc.record_result(_result(0, errors=[CANCELLED_ERROR]))
        acc.mark_cancel_requested()

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.CANCELLED
        assert (receipt.items_completed, receipt.items_failed) == (1, 0)
        assert receipt.notes_added == 2

    def test_counts_can_be_recorded_without_a_result_object(self):
        """The Batch queue worker reports per-item counts, never a result."""
        acc = _accumulator(2)
        acc.record_counts(notes_added=7, failed=False)
        acc.record_counts(notes_added=3, failed=True)

        receipt = _finish(acc)

        assert (receipt.items_completed, receipt.items_failed) == (1, 1)
        assert receipt.notes_added == 10
        assert receipt.note_ids == ()


class TestOutcome:
    def test_cancel_wins_over_failures(self):
        acc = _accumulator(2)
        acc.record_result(None, "boom")
        acc.mark_cancel_requested()

        assert _finish(acc).outcome is TerminalOutcome.CANCELLED

    def test_a_run_level_fatal_fails_a_run_with_no_items(self):
        acc = _accumulator(4)
        acc.mark_failed()

        receipt = _finish(acc)

        assert receipt.outcome is TerminalOutcome.FAILED
        assert receipt.items_completed == 0


class TestElapsed:
    def test_elapsed_is_the_monotonic_span(self):
        acc = _accumulator(1)
        acc.record_result(_result(1))

        receipt = _finish(acc, after=2412.0)

        assert receipt.duration.active_s == 2412.0
        assert receipt.duration.suspended is False

    def test_a_sleeping_machine_is_excluded_and_flagged(self):
        """The wall clock ran; the monotonic clock did not. That gap is sleep."""
        acc = _accumulator(1)

        receipt = _finish(acc, after=497.0, slept=3600.0)

        assert receipt.duration.active_s == 497.0
        assert receipt.duration.suspended_s == 3600.0
        assert receipt.duration.suspended is True

    def test_ordinary_clock_jitter_is_not_called_sleep(self):
        acc = _accumulator(1)

        receipt = _finish(acc, after=30.0, slept=SUSPEND_GAP_S - 1)

        assert receipt.duration.suspended is False


class TestDetails:
    def test_the_aggregate_result_carries_every_note_id(self):
        acc = _accumulator(2)
        acc.record_result(_result(2, ids=[1, 2]))
        acc.record_result(_result(1, ids=[3]))

        aggregate = _finish(acc).aggregate_result()

        assert aggregate is not None
        assert aggregate.card_ids == [1, 2, 3]
        assert aggregate.cards_created == 3
        assert aggregate.new_words_found == 3
        assert aggregate.elapsed_time == 60.0

    def test_the_aggregate_result_collects_every_failure(self):
        acc = _accumulator(2)
        acc.record_result(_result(1, ids=[1]))
        acc.record_result(_result(0, errors=["deck missing"]))

        aggregate = _finish(acc).aggregate_result()

        assert aggregate is not None
        assert aggregate.errors == ["deck missing"]
        assert aggregate.success is False

    def test_the_aggregate_result_carries_the_receipt_outcome(self):
        acc = _accumulator(2)
        acc.record_result(_result(1, ids=[1]))
        acc.record_result(_result(0, errors=["deck missing"]))

        aggregate = _finish(acc).aggregate_result()

        assert aggregate is not None
        assert getattr(aggregate, "terminal_outcome", None) is TerminalOutcome.PARTIAL

    def test_a_run_without_result_objects_has_no_details(self):
        acc = _accumulator(1)
        acc.record_counts(notes_added=3, failed=False)

        receipt = _finish(acc)

        assert receipt.has_details is False
        assert receipt.aggregate_result() is None

    def test_mined_forms_survive_into_the_aggregate_for_undo(self):
        acc = _accumulator(1)
        result = _result(1, ids=[9])
        result.mined_forms = ["食べる"]
        acc.record_result(result)

        aggregate = _finish(acc).aggregate_result()

        assert aggregate is not None
        assert aggregate.mined_forms == ["食べる"]


class TestWhitelist:
    def test_coverage_folds_across_items_and_mined_outranks_known(self):
        acc = _accumulator(2)
        first = _result(1, ids=[1])
        first.whitelist_coverage = WhitelistCoverage(frozenset({"a", "b", "c"}), mined=frozenset({"a"}))
        second = _result(1, ids=[2])
        second.whitelist_coverage = WhitelistCoverage(
            frozenset({"a", "b", "c"}), mined=frozenset({"b"}), known=frozenset({"a"})
        )
        acc.record_result(first)
        acc.record_result(second)

        coverage = _finish(acc).whitelist

        assert coverage is not None
        assert coverage.mined == {"a", "b"}
        assert coverage.known == frozenset()
        assert coverage.missing == {"c"}

    def test_a_counts_only_run_can_still_report_coverage(self):
        acc = _accumulator(1)
        acc.record_counts(notes_added=3, failed=False)
        acc.record_whitelist(WhitelistCoverage(frozenset({"a", "b"}), mined=frozenset({"a"})))

        receipt = _finish(acc)

        assert receipt.has_details is False
        assert receipt.whitelist is not None
        assert receipt.whitelist.missing == {"b"}

    def test_a_run_without_a_whitelist_reports_none(self):
        acc = _accumulator(2)
        acc.record_result(_result(1, ids=[1]))
        acc.record_result(SimpleNamespace(cards_created=1, card_ids=[2], errors=[], whitelist_coverage=object()))
        acc.record_whitelist(None)

        assert _finish(acc).whitelist is None
