"""Results state what changed; they do not celebrate (D47-B).

Every sentence a finished piece of work says is composed here, so this file is
where the voice is pinned. Two things are being defended: the wording itself,
and the fact that there is exactly one copy of it — a formatter that lives in
two places drifts, and the half a user sees depends on which screen they used.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")


from anki_miner.gui.utils import result_copy
from anki_miner.models.processing import TerminalOutcome


class TestCreatedCards:
    def test_it_names_the_deck_the_cards_went_to(self):
        assert result_copy.created_cards(42, "Mining") == "Created 42 cards in 'Mining'"

    def test_one_card_is_not_one_cards(self):
        assert result_copy.created_cards(1, "Mining") == "Created 1 card in 'Mining'"

    def test_a_caller_without_a_deck_gets_the_short_form(self):
        assert result_copy.created_cards(42) == "Created 42 cards"
        assert result_copy.created_cards(1) == "Created 1 card"

    def test_an_empty_deck_name_is_not_quoted_into_the_sentence(self):
        assert result_copy.created_cards(3, "") == "Created 3 cards"

    def test_zero_is_a_result_not_an_error(self):
        assert result_copy.created_cards(0, "Mining") == "No cards created."

    def test_a_negative_count_cannot_produce_a_celebration(self):
        assert result_copy.created_cards(-1) == "No cards created."


class TestTheCalmZeroCase:
    def test_it_states_the_outcome_before_the_reason(self):
        assert result_copy.nothing_new_to_mine() == "No cards created. Every word is already in Anki."

    def test_the_episode_processor_says_the_same_sentence(self):
        """Orchestration cannot import the GUI, so the copy is duplicated.

        That is fine as long as it cannot drift — which is what this asserts,
        against the source text Qt actually extracts rather than against a
        runtime call that would echo whatever literal it was handed.
        """
        from pathlib import Path

        import anki_miner.orchestration.episode_processor as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert f'"{result_copy.nothing_new_to_mine()}"' in source
        assert "All words already in Anki!" not in source


class TestCopied:
    def test_it_has_no_exclamation_mark(self):
        assert result_copy.copied() == "Copied"


class TestRunSummary:
    """The exact lines the shipped inline receipt renders (D20)."""

    def test_a_complete_multi_item_run(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.SUCCESS,
                items_completed=12,
                items_total=12,
                item_noun="episodes",
                notes_added=486,
                duration="40m 12s",
            )
            == "Mining complete — 12 episodes, 486 notes added in 40m 12s"
        )

    def test_a_cancelled_run_still_reports_what_it_did(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.CANCELLED,
                items_completed=3,
                items_total=12,
                item_noun="episodes",
                notes_added=84,
                duration="08m 17s",
            )
            == "Cancelled — 3 of 12 episodes completed; 84 notes added in 08m 17s"
        )

    def test_a_partly_failed_run_says_so(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.PARTIAL,
                items_completed=10,
                items_total=12,
                item_noun="videos",
                notes_added=400,
                duration="01m 30s",
            )
            == "Finished with errors — 10 of 12 videos completed; 400 notes added in 01m 30s"
        )

    def test_a_failed_run_says_so(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.FAILED,
                items_completed=0,
                items_total=12,
                item_noun="books",
                notes_added=0,
                duration="00m 05s",
            )
            == "Mining failed — 0 of 12 books completed; 0 notes added in 00m 05s"
        )

    def test_a_single_item_run_never_says_one_episodes(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.SUCCESS,
                items_completed=1,
                items_total=1,
                item_noun="episodes",
                notes_added=7,
                duration="01m 03s",
            )
            == "Mining complete — 7 notes added in 01m 03s"
        )

    def test_an_empty_noun_suppresses_the_count(self):
        assert (
            result_copy.run_summary(
                TerminalOutcome.SUCCESS,
                items_completed=4,
                items_total=12,
                item_noun="",
                notes_added=7,
                duration="01m 03s",
            )
            == "Mining complete — 7 notes added in 01m 03s"
        )

    def test_a_slept_run_says_the_time_is_active_time(self):
        line = result_copy.run_summary(
            TerminalOutcome.SUCCESS,
            items_completed=1,
            items_total=1,
            item_noun="",
            notes_added=3,
            duration="02m 00s",
            suspended=True,
        )
        assert line.endswith(" (asleep time excluded)")


def test_the_receipt_model_holds_no_wording():
    """W6-T15 owns the words; W1-T8 owns the numbers. No formatter in the model."""
    from pathlib import Path

    import anki_miner.gui.controllers.run_receipt as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "QCoreApplication.translate" not in source
    assert "self.tr(" not in source


class TestTheCallSitesUseIt:
    """A formatter nobody calls is a formatter that drifts from the screens."""

    def test_the_log_console_says_copied_without_an_exclamation(self, qtbot):
        from anki_miner.gui.widgets.log_widget import LogWidget

        widget = LogWidget()
        qtbot.addWidget(widget)
        widget.append_info("something worth keeping")
        widget._on_copy_all_clicked()

        assert widget.copy_all_button.text() == "Copied"

    def test_the_results_dialog_header_states_what_it_made(self, qtbot):
        from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
        from anki_miner.models import ProcessingResult

        dialog = ResultsDialog(ProcessingResult(total_words_found=90, new_words_found=42, cards_created=42))
        qtbot.addWidget(dialog)

        assert dialog._title_label.text() == "Created 42 cards"

    def test_a_run_that_made_nothing_does_not_claim_success(self, qtbot):
        from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
        from anki_miner.models import ProcessingResult

        dialog = ResultsDialog(ProcessingResult(total_words_found=90, new_words_found=0, cards_created=0))
        qtbot.addWidget(dialog)

        assert dialog._title_label.text() == "No cards created."
