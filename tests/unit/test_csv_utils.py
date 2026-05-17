"""Tests for anki_miner.utils.csv_utils."""

from anki_miner.utils.csv_utils import detect_delimiter, is_header_row


class TestDetectDelimiter:
    """Tests for detect_delimiter."""

    def test_tab_separated(self):
        sample = "word\trank\nの\t1\nに\t2\n"
        assert detect_delimiter(sample) == "\t"

    def test_comma_separated(self):
        sample = "word,rank\nの,1\nに,2\n"
        assert detect_delimiter(sample) == ","

    def test_semicolon_not_detected_falls_back_to_comma(self):
        # semicolons are not in the detection logic; with no tabs or commas,
        # comma_count == tab_count == 0, so the tie-break returns ","
        sample = "word;rank\nの;1\nに;2\n"
        assert detect_delimiter(sample) == ","

    def test_tab_wins_when_more_tabs_than_commas(self):
        # one comma buried in a field, but many tabs
        sample = "a\tb\tc\nsome,word\t1\t2\n"
        assert detect_delimiter(sample) == "\t"

    def test_comma_wins_when_more_commas_than_tabs(self):
        sample = "a,b,c\n1,2,3\n"
        assert detect_delimiter(sample) == ","


class TestIsHeaderRow:
    """Tests for is_header_row."""

    def test_row_with_word_keyword_is_header(self):
        assert is_header_row(["word", "rank"]) is True

    def test_row_with_frequency_keyword_is_header(self):
        assert is_header_row(["frequency", "lemma"]) is True

    def test_row_with_reading_keyword_is_header(self):
        assert is_header_row(["reading", "kanji", "pattern"]) is True

    def test_row_with_rank_keyword_is_header(self):
        assert is_header_row(["rank", "word"]) is True

    def test_keywords_are_case_insensitive(self):
        assert is_header_row(["Word", "Rank"]) is True
        assert is_header_row(["FREQUENCY"]) is True

    def test_keywords_tolerate_surrounding_whitespace(self):
        assert is_header_row(["  word  ", "  rank  "]) is True

    def test_numeric_data_row_is_not_header(self):
        # typical rank,word row: neither cell is a header keyword
        assert is_header_row(["1", "食べる"]) is False

    def test_japanese_word_row_is_not_header(self):
        assert is_header_row(["の", "1"]) is False

    def test_empty_row_is_not_header(self):
        assert is_header_row([]) is False

    def test_partial_match_in_row_triggers_header(self):
        # only one cell needs to match
        assert is_header_row(["の", "kana"]) is True
