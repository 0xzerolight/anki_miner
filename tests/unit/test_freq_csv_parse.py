"""Tests for the shared frequency CSV/normalizer module."""

from __future__ import annotations

from anki_miner.services.frequency.csv_parse import (
    extract_envelope_reading,
    normalize_freq_rank,
)


class TestNormalizeFreqRank:
    def test_plain_int(self) -> None:
        assert normalize_freq_rank(5) == 5

    def test_numeric_string(self) -> None:
        assert normalize_freq_rank(" 12 ") == 12

    def test_bool_rejected(self) -> None:
        assert normalize_freq_rank(True) is None

    def test_zero_and_negative_rejected(self) -> None:
        assert normalize_freq_rank(0) is None
        assert normalize_freq_rank(-3) is None

    def test_display_only_marker_rejected(self) -> None:
        assert normalize_freq_rank("①") is None
        assert normalize_freq_rank("高") is None

    def test_frequency_envelope(self) -> None:
        assert normalize_freq_rank({"reading": "いく", "frequency": 9}) == 9

    def test_value_envelope(self) -> None:
        assert normalize_freq_rank({"value": 7, "displayValue": "7"}) == 7

    def test_value_envelope_bool_rejected(self) -> None:
        assert normalize_freq_rank({"value": True}) is None


class TestExtractEnvelopeReading:
    def test_bccwj_reading(self) -> None:
        assert extract_envelope_reading({"reading": "いく", "frequency": 9}) == "いく"

    def test_empty_reading_is_none(self) -> None:
        assert extract_envelope_reading({"reading": "  ", "frequency": 9}) is None

    def test_no_reading_key(self) -> None:
        assert extract_envelope_reading({"value": 7}) is None

    def test_non_dict(self) -> None:
        assert extract_envelope_reading(5) is None
        assert extract_envelope_reading("①") is None
