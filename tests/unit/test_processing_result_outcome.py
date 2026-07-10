"""Tests for :func:`classify_result` — the queue-result outcome classifier.

A non-raising ``process_*`` return must be routed as SUCCESS (clean),
CANCELLED (Stop mid-mine → re-minable), or FAILED (errors present). The
classifier only honours a genuine ``list`` ``errors`` so bare test stand-ins
(``MagicMock``/``SimpleNamespace``) keep classifying as SUCCESS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from anki_miner.models import ProcessingResult
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
