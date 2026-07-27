"""Tests for :func:`classify_result` — the queue-result outcome classifier.

A non-raising ``process_*`` return must be routed as SUCCESS (clean),
CANCELLED (Stop mid-mine → re-minable), or FAILED (errors present). The
classifier only honours a genuine ``list`` ``errors`` so bare test stand-ins
(``MagicMock``/``SimpleNamespace``) keep classifying as SUCCESS.

Also covers the Anki note-write provenance a failed result carries
(``anki_write_state`` / ``failure_is_transient``) and the derived
``auto_retry_eligible`` predicate that gates automatic retry.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.models import AnkiWriteState, ProcessingResult
from anki_miner.models.processing import (
    CANCELLED_ERROR,
    MiningOutcome,
    classify_result,
)


def test_clean_result_is_success():
    result = ProcessingResult(total_words_found=1, new_words_found=1, cards_created=1)
    assert classify_result(result) is MiningOutcome.SUCCESS


def test_errors_result_is_failed():
    result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["ffmpeg exploded"])
    assert classify_result(result) is MiningOutcome.FAILED


def test_cancelled_marker_is_cancelled():
    result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=[CANCELLED_ERROR])
    assert classify_result(result) is MiningOutcome.CANCELLED


def test_none_result_is_failed():
    assert classify_result(None) is MiningOutcome.FAILED


def test_magicmock_stand_in_is_success():
    # A bare MagicMock's .errors is a truthy Mock, not a list — must not be
    # mistaken for a failure (the historical queue-site behaviour).
    assert classify_result(MagicMock(cards_created=5)) is MiningOutcome.SUCCESS


def test_simplenamespace_without_errors_is_success():
    assert classify_result(SimpleNamespace(cards_created=3)) is MiningOutcome.SUCCESS


def test_partial_cards_with_errors_still_failed():
    result = ProcessingResult(total_words_found=5, new_words_found=5, cards_created=2, errors=["anki went away"])
    assert classify_result(result) is MiningOutcome.FAILED
    assert result.cards_created == 2


class TestAnkiWriteProvenance:
    """Defaults and retry semantics of the note-write provenance fields."""

    def _result(self, **overrides) -> ProcessingResult:
        return ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, **overrides)

    def test_write_state_defaults_to_uncertain(self):
        """A result nobody stamped must claim the UNSAFE answer, not the safe one.

        Defaulting to NO_NOTE_WRITE would let any hand-built or partially
        constructed result assert a proof it never made.
        """
        assert self._result().anki_write_state is AnkiWriteState.NOTE_WRITE_UNCERTAIN

    def test_failure_is_transient_defaults_false(self):
        assert self._result().failure_is_transient is False

    def test_default_result_is_not_auto_retryable(self):
        assert self._result().auto_retry_eligible is False

    def test_transient_failure_before_any_write_is_retryable(self):
        result = self._result(
            errors=["Cannot connect to AnkiConnect. Is Anki running?"],
            failure_is_transient=True,
            anki_write_state=AnkiWriteState.NO_NOTE_WRITE,
        )
        assert result.auto_retry_eligible is True

    @pytest.mark.parametrize(
        "state",
        [AnkiWriteState.NOTE_WRITE_UNCERTAIN, AnkiWriteState.NOTE_WRITE_CONFIRMED],
    )
    def test_transient_failure_after_a_possible_write_is_not_retryable(self, state):
        """Fail closed: a replay could duplicate the user's cards."""
        result = self._result(failure_is_transient=True, anki_write_state=state)
        assert result.auto_retry_eligible is False

    def test_non_transient_failure_is_not_retryable_even_with_no_write(self):
        result = self._result(failure_is_transient=False, anki_write_state=AnkiWriteState.NO_NOTE_WRITE)
        assert result.auto_retry_eligible is False

    def test_truthy_stand_ins_are_not_mistaken_for_eligibility(self):
        """Identity checks, not truthiness — a MagicMock must never unlock retry."""
        result = self._result()
        result.failure_is_transient = MagicMock()  # type: ignore[assignment]
        result.anki_write_state = MagicMock()  # type: ignore[assignment]
        assert result.auto_retry_eligible is False

    def test_write_state_string_value_is_not_the_enum(self):
        """Enum identity, not the ``"no_note_write"`` string, unlocks retry."""
        result = self._result(failure_is_transient=True)
        result.anki_write_state = "no_note_write"  # type: ignore[assignment]
        assert result.auto_retry_eligible is False

    def test_states_are_three_distinct_members(self):
        assert len({s.value for s in AnkiWriteState}) == 3
