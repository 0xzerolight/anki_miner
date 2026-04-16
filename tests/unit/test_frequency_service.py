"""Tests for FrequencyService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency_service import FrequencyService


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


class TestLookupBatch:
    """Tests for batch lookup."""

    def test_returns_results_in_order(self, tmp_path):
        """Test batch lookup returns results in the same order as input."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text(
            "1,の\n" "100,食べる\n",
            encoding="utf-8",
        )
        service = FrequencyService(csv_file)
        service.load()

        results = service.lookup_batch(["食べる", "unknown", "の"])
        assert results == [100, None, 1]

    def test_empty_batch(self, tmp_path):
        """Test batch lookup with empty list returns empty list."""
        csv_file = tmp_path / "freq.csv"
        csv_file.write_text("1,食べる\n", encoding="utf-8")
        service = FrequencyService(csv_file)
        service.load()

        assert service.lookup_batch([]) == []


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
