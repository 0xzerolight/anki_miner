"""Tests for the shared frequency CSV/normalizer module."""

from __future__ import annotations

from anki_miner.services.frequency.csv_parse import (
    _extract_word_rank,
    _is_word_first_header,
    extract_envelope_reading,
    normalize_freq_rank,
)


class TestNormalizeFreqRank:
    """normalize_freq_rank returns ``(rank, display_value)`` (Yomitan displayValue
    triple, collapsed to the two fields anki_miner stores)."""

    def test_plain_int_has_no_display(self) -> None:
        assert normalize_freq_rank(5) == (5, None)

    def test_numeric_string_keeps_string_as_display(self) -> None:
        # String payload: the whole (stripped) string is the display value.
        assert normalize_freq_rank(" 12 ") == (12, "12")

    def test_string_with_separator_extracts_leading_number(self) -> None:
        # "1099/72000" is no longer rejected: rank=1099, display="1099/72000".
        assert normalize_freq_rank("1099/72000") == (1099, "1099/72000")

    def test_jpdb_marker_string_extracts_number(self) -> None:
        assert normalize_freq_rank("1234㋕") == (1234, "1234㋕")

    def test_float_string_truncates_to_int_rank(self) -> None:
        assert normalize_freq_rank("3.9位") == (3, "3.9位")

    def test_bool_rejected(self) -> None:
        assert normalize_freq_rank(True) == (None, None)

    def test_zero_and_negative_rejected(self) -> None:
        assert normalize_freq_rank(0) == (None, None)
        assert normalize_freq_rank(-3) == (None, None)
        # A string whose leading number is <= 0 is unusable as a rank.
        assert normalize_freq_rank("0/5000") == (None, None)

    def test_display_only_marker_rejected(self) -> None:
        # No float-shaped run -> no rank -> skipped (display discarded).
        assert normalize_freq_rank("①") == (None, None)
        assert normalize_freq_rank("高") == (None, None)

    def test_frequency_envelope(self) -> None:
        assert normalize_freq_rank({"reading": "いく", "frequency": 9}) == (9, None)

    def test_frequency_envelope_with_string_frequency(self) -> None:
        assert normalize_freq_rank({"reading": "いく", "frequency": "9/500"}) == (9, "9/500")

    def test_value_envelope_keeps_display_value(self) -> None:
        assert normalize_freq_rank({"value": 7, "displayValue": "7位"}) == (7, "7位")

    def test_value_envelope_no_display(self) -> None:
        assert normalize_freq_rank({"value": 7}) == (7, None)

    def test_value_envelope_string_value_parsed(self) -> None:
        assert normalize_freq_rank({"value": "42x", "displayValue": "42x"}) == (42, "42x")

    def test_value_envelope_bool_rejected(self) -> None:
        assert normalize_freq_rank({"value": True}) == (None, None)


class TestIsWordFirstHeader:
    """Unit tests for _is_word_first_header (ported from the removed FrequencyService suite)."""

    def test_term_header(self) -> None:
        assert _is_word_first_header(["term", "rank"]) is True

    def test_word_header(self) -> None:
        assert _is_word_first_header(["word", "rank"]) is True

    def test_case_insensitive(self) -> None:
        assert _is_word_first_header(["Term", "Rank"]) is True
        assert _is_word_first_header(["WORD", "RANK"]) is True

    def test_rank_first_is_not_word_first(self) -> None:
        assert _is_word_first_header(["rank", "word"]) is False

    def test_empty_row(self) -> None:
        assert _is_word_first_header([]) is False

    def test_unknown_header(self) -> None:
        assert _is_word_first_header(["frequency", "lemma"]) is False


class TestExtractWordRankWordFirst:
    """Unit tests for _extract_word_rank with word_first=True (OVH-034)."""

    def test_normal_word_rank(self) -> None:
        assert _extract_word_rank(["食べる", "100"], word_first=True) == ("食べる", 100)

    def test_fullwidth_digit_term_not_swapped(self) -> None:
        """Fullwidth-digit term '１０' must NOT be misread as rank 10."""
        word, rank = _extract_word_rank(["１０", "42"], word_first=True)
        assert word == "１０"
        assert rank == 42

    def test_ascii_digit_term_not_swapped(self) -> None:
        """Pure-ASCII digit term '2020' must NOT be misread as rank 2020."""
        word, rank = _extract_word_rank(["2020", "5"], word_first=True)
        assert word == "2020"
        assert rank == 5

    def test_fallback_on_bad_rank_col(self) -> None:
        """If col-1 is not an int, return ('', None) rather than guessing."""
        assert _extract_word_rank(["食べる", "notanint"], word_first=True) == ("", None)

    def test_empty_word_returns_empty(self) -> None:
        assert _extract_word_rank(["", "42"], word_first=True) == ("", None)

    def test_multi_column_word_first_scans_for_first_int(self) -> None:
        """3+ columns: col-0 is the word, first numeric of the rest is the rank."""
        assert _extract_word_rank(["食べる", "たべる", "100"], word_first=True) == ("食べる", 100)


class TestExtractWordRankAutoDetect:
    """Legacy auto-detect (word_first=False) is unchanged."""

    def test_rank_word_order(self) -> None:
        assert _extract_word_rank(["1", "の"]) == ("の", 1)

    def test_word_rank_order(self) -> None:
        assert _extract_word_rank(["食べる", "100"]) == ("食べる", 100)

    def test_fullwidth_digit_term_is_swapped_without_word_first(self) -> None:
        """Without word_first, '１０' is parsed as rank 10 (pre-existing behaviour).

        Python int() parses fullwidth digits, so col-0 succeeds as a rank.
        """
        word, rank = _extract_word_rank(["１０", "42"])
        assert rank == 10
        assert word == "42"

    def test_multi_column_finds_word_and_rank(self) -> None:
        """3+ columns with no word-first header: first non-numeric is the word."""
        assert _extract_word_rank(["食べる", "たべる", "100"]) == ("食べる", 100)


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
