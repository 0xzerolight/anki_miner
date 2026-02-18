"""Tests for PitchAccentService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.pitch_accent_service import PitchAccentService


class TestLoad:
    """Tests for loading pitch accent data."""

    def test_loads_valid_csv(self, tmp_path):
        """Test loading a valid Kanjium-format CSV."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "のむ,飲む,1\n" "みる,見る,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        assert service.load() is True
        assert service.is_available() is True

    def test_raises_setup_error_when_file_missing(self, tmp_path):
        """Test that SetupError is raised when file is missing."""
        service = PitchAccentService(tmp_path / "nonexistent.csv")
        with pytest.raises(SetupError):
            service.load()

    def test_handles_malformed_rows_gracefully(self, tmp_path):
        """Test that rows with fewer than 3 columns are skipped."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "incomplete\n" "also,incomplete\n" "のむ,飲む,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("飲む") == "1"

    def test_first_entry_wins_on_duplicate_key(self, tmp_path):
        """Test that the first entry wins when keys are duplicated."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "たべる,食べる,2\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"

    def test_empty_csv_file(self, tmp_path):
        """Test that an empty CSV file loads with zero entries."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("", encoding="utf-8")

        service = PitchAccentService(csv_file)
        assert service.load() is True
        assert service.is_available() is True
        assert service.lookup("食べる") is None

    def test_generic_exception_raises_setup_error(self, tmp_path):
        """Test that a generic error during load raises SetupError."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_bytes(b"\x80\x81\x82\xff\xfe")  # Invalid UTF-8

        service = PitchAccentService(csv_file)
        with pytest.raises(SetupError, match="Error loading pitch accent data"):
            service.load()


class TestLookup:
    """Tests for pitch accent lookup."""

    @pytest.fixture
    def loaded_service(self, tmp_path):
        """Create a loaded PitchAccentService."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "のむ,飲む,1\n" "はしる,走る,2\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        return service

    def test_returns_pattern_for_known_word(self, loaded_service):
        """Test lookup returns pattern for a known kanji word."""
        assert loaded_service.lookup("食べる") == "0"
        assert loaded_service.lookup("飲む") == "1"

    def test_returns_none_for_unknown_word(self, loaded_service):
        """Test lookup returns None for an unknown word."""
        assert loaded_service.lookup("存在しない") is None

    def test_falls_back_to_reading_when_kanji_not_found(self, tmp_path):
        """Test lookup falls back to reading when kanji is not found."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        # Look up a word that doesn't exist, with a reading that does
        assert service.lookup("不明", reading="たべる") == "0"

    def test_returns_none_when_not_loaded(self, tmp_path):
        """Test lookup returns None when data hasn't been loaded."""
        service = PitchAccentService(tmp_path / "pitch.csv")
        assert service.lookup("食べる") is None


class TestLookupBatch:
    """Tests for batch lookup."""

    def test_returns_results_in_order(self, tmp_path):
        """Test batch lookup returns results in the same order as input."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "のむ,飲む,1\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()

        results = service.lookup_batch(
            [
                ("食べる", "たべる"),
                ("unknown", ""),
                ("飲む", "のむ"),
            ]
        )
        assert results == ["0", None, "1"]

    def test_empty_batch(self, tmp_path):
        """Test batch lookup with empty list returns empty list."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()

        assert service.lookup_batch([]) == []


class TestIsAvailable:
    """Tests for is_available."""

    def test_false_before_load(self, tmp_path):
        """Test is_available returns False before loading."""
        service = PitchAccentService(tmp_path / "pitch.csv")
        assert service.is_available() is False

    def test_true_after_load(self, tmp_path):
        """Test is_available returns True after successful loading."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        assert service.is_available() is True
