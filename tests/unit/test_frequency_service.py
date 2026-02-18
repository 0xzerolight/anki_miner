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
