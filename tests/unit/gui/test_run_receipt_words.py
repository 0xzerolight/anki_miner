"""WP-34: View details gets the run's mined and not-mined words, folded over every item."""

from __future__ import annotations

from anki_miner.gui.controllers.run_receipt import RunReceiptAccumulator
from anki_miner.models.processing import NotMinedReason, NotMinedReport, ProcessingResult


def _result(cards: int, report: NotMinedReport) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 2,
        new_words_found=cards,
        cards_created=cards,
        card_ids=list(range(1, cards + 1)),
        not_mined=report,
    )


def test_the_aggregate_result_carries_the_folded_report():
    acc = RunReceiptAccumulator(2, monotonic_start=100.0, wall_start=100.0)
    acc.record_result(_result(1, NotMinedReport({NotMinedReason.KNOWN: frozenset({"猫"})}, mined=frozenset({"走る"}))))
    acc.record_result(_result(0, NotMinedReport({NotMinedReason.NO_DEFINITION: frozenset({"鳥"})})))

    receipt = acc.finish(monotonic_now=160.0, wall_now=160.0)
    aggregate = receipt.aggregate_result()

    assert aggregate is not None
    assert aggregate.not_mined == receipt.not_mined
    assert aggregate.not_mined.mined == frozenset({"走る"})
    assert aggregate.not_mined.forms(NotMinedReason.NO_DEFINITION) == frozenset({"鳥"})


def test_a_run_without_reports_has_none():
    acc = RunReceiptAccumulator(1, monotonic_start=100.0, wall_start=100.0)
    acc.record_result(ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0))

    aggregate = acc.finish(monotonic_now=101.0, wall_now=101.0).aggregate_result()

    assert aggregate is not None
    assert aggregate.not_mined is None
