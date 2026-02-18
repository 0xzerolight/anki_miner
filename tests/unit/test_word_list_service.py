"""Tests for WordListService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.word_list_service import WordListService


class TestLoad:
    """Tests for load method."""

    def test_loads_blacklist(self, tmp_path):
        """Should read words from a blacklist file."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text("食べる\n飲む\n走る\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.get_blacklist() == {"食べる", "飲む", "走る"}

    def test_loads_whitelist(self, tmp_path):
        """Should read words from a whitelist file."""
        wl = tmp_path / "whitelist.txt"
        wl.write_text("新しい\n古い\n", encoding="utf-8")

        service = WordListService(whitelist_path=wl)
        service.load()

        assert service.get_whitelist() == {"新しい", "古い"}

    def test_ignores_blank_lines_and_comments(self, tmp_path):
        """Should skip blank lines and lines starting with #."""
        bl = tmp_path / "blacklist.txt"
        bl.write_text(
            "# This is a comment\n食べる\n\n# Another comment\n飲む\n  \n",
            encoding="utf-8",
        )

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.get_blacklist() == {"食べる", "飲む"}

    def test_missing_file_raises_setup_error(self, tmp_path):
        """Should raise SetupError for nonexistent file."""
        service = WordListService(blacklist_path=tmp_path / "nonexistent.txt")

        with pytest.raises(SetupError, match="not found"):
            service.load()

    def test_empty_file(self, tmp_path):
        """Should return empty set for empty file."""
        bl = tmp_path / "empty.txt"
        bl.write_text("", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.get_blacklist() == set()

    def test_none_paths_skip_loading(self):
        """Should succeed with no files when paths are None."""
        service = WordListService(blacklist_path=None, whitelist_path=None)
        service.load()

        assert service.get_blacklist() == set()
        assert service.get_whitelist() == set()
        assert service.is_available() is True


class TestIsAvailable:
    """Tests for is_available method."""

    def test_false_before_load(self, tmp_path):
        """Should return False before load is called."""
        service = WordListService(blacklist_path=tmp_path / "bl.txt")
        assert service.is_available() is False

    def test_true_after_load(self, tmp_path):
        """Should return True after successful load."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_available() is True


class TestBlacklist:
    """Tests for blacklist lookups."""

    def test_is_blacklisted(self, tmp_path):
        """Should return True for blacklisted words."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n飲む\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_blacklisted("走る") is False

    def test_get_blacklist_returns_copy(self, tmp_path):
        """get_blacklist should return a copy, not the internal set."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl)
        service.load()

        result = service.get_blacklist()
        result.add("extra")
        assert "extra" not in service.get_blacklist()


class TestWhitelist:
    """Tests for whitelist lookups."""

    def test_is_whitelisted(self, tmp_path):
        """Should return True for whitelisted words."""
        wl = tmp_path / "wl.txt"
        wl.write_text("新しい\n古い\n", encoding="utf-8")

        service = WordListService(whitelist_path=wl)
        service.load()

        assert service.is_whitelisted("新しい") is True
        assert service.is_whitelisted("食べる") is False


class TestBothLists:
    """Tests with both blacklist and whitelist loaded."""

    def test_independent_lists(self, tmp_path):
        """Blacklist and whitelist should be independent sets."""
        bl = tmp_path / "bl.txt"
        bl.write_text("食べる\n", encoding="utf-8")
        wl = tmp_path / "wl.txt"
        wl.write_text("飲む\n", encoding="utf-8")

        service = WordListService(blacklist_path=bl, whitelist_path=wl)
        service.load()

        assert service.is_blacklisted("食べる") is True
        assert service.is_whitelisted("食べる") is False
        assert service.is_blacklisted("飲む") is False
        assert service.is_whitelisted("飲む") is True
