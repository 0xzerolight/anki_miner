"""Tests for FrequencyService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency_service import (
    FrequencyService,
    _extract_word_rank,
    _is_word_first_header,
)


class TestLoad:
    """Tests for loading frequency data."""

    def test_loads_rank_word_format(self, tmp_path):
        """Test loading CSV with rank, word format."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,の\n" "2,に\n" "3,は\n" "100,食べる\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        assert service.load() is True
        assert service.is_available() is True
        assert service.lookup("食べる") == 100

    def test_loads_word_rank_format(self, tmp_path):
        """Test loading CSV with word, rank format (auto-detection)."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "の,1\n" "に,2\n" "食べる,100\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100

    def test_raises_setup_error_when_file_missing(self, tmp_path):
        """Test that SetupError is raised when file is missing."""
        service = FrequencyService(tmp_path / "nonexistent.csv")
        with pytest.raises(SetupError):
            service.load()

    def test_handles_malformed_rows(self, tmp_path):
        """Test that malformed rows are skipped."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,食べる\n" "bad\n" "not,a,number\n" "2,飲む\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("食べる") == 1
        assert service.lookup("飲む") == 2

    def test_loads_tsv_rank_word_format(self, tmp_path):
        """Test loading TSV with rank, word format."""
        tsv_file = tmp_path / "freq.tsv"
        tsv_file.write_text(
            "1\tの\n" "2\tに\n" "100\t食べる\n",
            encoding="utf-8",
        )

        service = FrequencyService(tsv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100

    def test_loads_tsv_word_rank_format(self, tmp_path):
        """Test loading TSV with word, rank format."""
        tsv_file = tmp_path / "freq.tsv"
        tsv_file.write_text(
            "の\t1\n" "に\t2\n" "食べる\t100\n",
            encoding="utf-8",
        )

        service = FrequencyService(tsv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100

    def test_skips_header_row(self, tmp_path):
        """Test that a header row is automatically skipped."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "rank,word\n" "1,の\n" "2,に\n" "100,食べる\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100
        assert service.lookup("word") is None

    def test_skips_header_row_tsv(self, tmp_path):
        """Test that a header row is skipped in TSV files."""
        tsv_file = tmp_path / "freq.tsv"
        tsv_file.write_text(
            "frequency\tlemma\n" "1\tの\n" "100\t食べる\n",
            encoding="utf-8",
        )

        service = FrequencyService(tsv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100

    def test_loads_multi_column_tsv(self, tmp_path):
        """Test loading TSV with more than 2 columns (e.g. JPDB format: term, reading, freq, kana_freq)."""
        tsv_file = tmp_path / "freq.tsv"
        tsv_file.write_text(
            "term\treading\tfrequency\tkana_frequency\n"
            "食べる\tたべる\t100\t200\n"
            "飲む\tのむ\t50\t150\n"
            "の\tの\t1\t1\n",
            encoding="utf-8",
        )

        service = FrequencyService(tsv_file)
        service.load()
        assert service.lookup("食べる") == 100
        assert service.lookup("飲む") == 50
        assert service.lookup("の") == 1

    def test_entry_count_property(self, tmp_path):
        """Test that entry_count reflects number of loaded entries."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text("1,の\n2,に\n100,食べる\n", encoding="utf-8")

        service = FrequencyService(csv_file)
        assert service.entry_count == 0
        service.load()
        assert service.entry_count == 3

    def test_first_entry_wins_on_duplicate(self, tmp_path):
        """Test that the first entry wins when words are duplicated."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,食べる\n" "999,食べる\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("食べる") == 1

    def test_first_entry_wins_word_rank_format(self, tmp_path):
        """Test that first entry wins for duplicates in word-rank format."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "食べる,1\n" "食べる,999\n",
            encoding="utf-8",
        )

        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("食べる") == 1

    def test_empty_csv_file(self, tmp_path):
        """Test that an empty CSV file loads with zero entries."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text("", encoding="utf-8")

        service = FrequencyService(csv_file)
        assert service.load() is True
        assert service.is_available() is True
        assert service.lookup("食べる") is None


class TestLookup:
    """Tests for frequency lookup."""

    @pytest.fixture
    def loaded_service(self, tmp_path):
        """Create a loaded FrequencyService."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,の\n" "2,に\n" "100,食べる\n" "5000,飲む\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        return service

    def test_returns_rank_for_known_word(self, loaded_service):
        """Test lookup returns rank for a known word."""
        assert loaded_service.lookup("食べる") == 100
        assert loaded_service.lookup("の") == 1

    def test_returns_none_for_unknown_word(self, loaded_service):
        """Test lookup returns None for an unknown word."""
        assert loaded_service.lookup("存在しない") is None

    def test_returns_none_when_not_loaded(self, tmp_path):
        """Test lookup returns None when data hasn't been loaded."""
        service = FrequencyService(tmp_path / "freq.csv")
        assert service.lookup("食べる") is None


class TestIsAvailable:
    """Tests for is_available."""

    def test_false_before_load(self, tmp_path):
        """Test is_available returns False before loading."""
        service = FrequencyService(tmp_path / "freq.csv")
        assert service.is_available() is False

    def test_true_after_load(self, tmp_path):
        """Test is_available returns True after successful loading."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text("1,食べる\n", encoding="utf-8")
        service = FrequencyService(csv_file)
        service.load()
        assert service.is_available() is True


class TestLoadException:
    """Tests for error handling during load."""

    def test_load_raises_setup_error_on_exception(self, tmp_path):
        """Should raise SetupError when file reading fails."""
        from unittest.mock import patch

        csv_file = tmp_path / "freq.csv"
        csv_file.write_text("1,食べる\n", encoding="utf-8")
        service = FrequencyService(csv_file)

        with (
            patch("builtins.open", side_effect=PermissionError("access denied")),
            pytest.raises(SetupError, match="Error loading frequency data"),
        ):
            service.load()


class TestIsWordFirstHeader:
    """Unit tests for _is_word_first_header."""

    def test_term_header(self):
        assert _is_word_first_header(["term", "rank"]) is True

    def test_word_header(self):
        assert _is_word_first_header(["word", "rank"]) is True

    def test_case_insensitive(self):
        assert _is_word_first_header(["Term", "Rank"]) is True
        assert _is_word_first_header(["WORD", "RANK"]) is True

    def test_rank_first_is_not_word_first(self):
        assert _is_word_first_header(["rank", "word"]) is False

    def test_empty_row(self):
        assert _is_word_first_header([]) is False

    def test_unknown_header(self):
        assert _is_word_first_header(["frequency", "lemma"]) is False


class TestExtractWordRankWordFirst:
    """Unit tests for _extract_word_rank with word_first=True (OVH-034)."""

    def test_normal_word_rank(self):
        """Plain kana term is correctly extracted in word-first mode."""
        assert _extract_word_rank(["食べる", "100"], word_first=True) == ("食べる", 100)

    def test_fullwidth_digit_term_not_swapped(self):
        """Fullwidth-digit term '１０' must NOT be misread as rank 10."""
        word, rank = _extract_word_rank(["１０", "42"], word_first=True)
        assert word == "１０"
        assert rank == 42

    def test_ascii_digit_term_not_swapped(self):
        """Pure-ASCII digit term '2020' must NOT be misread as rank 2020."""
        word, rank = _extract_word_rank(["2020", "5"], word_first=True)
        assert word == "2020"
        assert rank == 5

    def test_fallback_on_bad_rank_col(self):
        """If col-1 is not an int, return ("", None) rather than guessing."""
        assert _extract_word_rank(["食べる", "notanint"], word_first=True) == ("", None)

    def test_empty_word_returns_empty(self):
        assert _extract_word_rank(["", "42"], word_first=True) == ("", None)


class TestExtractWordRankAutoDetect:
    """Ensure legacy auto-detect (word_first=False) is unchanged."""

    def test_rank_word_order(self):
        assert _extract_word_rank(["1", "の"]) == ("の", 1)

    def test_word_rank_order(self):
        assert _extract_word_rank(["食べる", "100"]) == ("食べる", 100)

    def test_fullwidth_digit_term_is_swapped_without_word_first(self):
        """Without word_first, '１０' is parsed as rank 10 (pre-existing behaviour)."""
        # Python int() parses fullwidth digits, so col-0 succeeds as rank.
        word, rank = _extract_word_rank(["１０", "42"])
        assert rank == 10
        assert word == "42"


class TestFrequencyServiceImporterHeader:
    """Integration: FrequencyService honours the importer's term,rank header (OVH-034)."""

    def test_digit_only_term_preserved_with_importer_header(self, tmp_path):
        """A digit-only term like '２０２０' is stored with correct rank."""
        csv_file = tmp_path / "freq.csv"
        # Mimic what yomitan_freq_importer.py writes: header + (term, rank) rows.
        csv_file.write_text(
            "term,rank\n" "２０２０,1\n" "食べる,2\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("２０２０") == 1
        assert service.lookup("食べる") == 2
        # The bogus swapped entry must NOT appear.
        assert service.lookup("1") is None

    def test_ascii_digit_term_preserved_with_importer_header(self, tmp_path):
        """A pure-ASCII digit term like '2020' is stored with the correct rank."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "term,rank\n" "2020,5\n" "飲む,10\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("2020") == 5
        assert service.lookup("飲む") == 10
        assert service.lookup("5") is None

    def test_word_header_also_forces_word_first(self, tmp_path):
        """A 'word,rank' header (user-exported variant) also forces word-first."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "word,rank\n" "１０,42\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("１０") == 42

    def test_headerless_legacy_rank_word_file_unchanged(self, tmp_path):
        """Headerless (rank,word) files still parse with legacy auto-detect."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,の\n" "2,に\n" "100,食べる\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100

    def test_headerless_legacy_word_rank_file_unchanged(self, tmp_path):
        """Headerless (word,rank) files still parse via auto-detect."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "の,1\n" "食べる,100\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()
        assert service.lookup("の") == 1
        assert service.lookup("食べる") == 100
