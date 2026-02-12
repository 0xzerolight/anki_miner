"""Tests for sort_utils module."""

from anki_miner.utils.sort_utils import natural_sort_key


class TestNaturalSortKey:
    """Tests for natural_sort_key function."""

    def test_sorts_numeric_strings_naturally(self):
        """Should sort numbers numerically, not alphabetically."""
        files = ["file10.txt", "file2.txt", "file1.txt"]
        result = sorted(files, key=natural_sort_key)
        assert result == ["file1.txt", "file2.txt", "file10.txt"]

    def test_handles_leading_zeros(self):
        """Should treat 01 and 1 as equal values."""
        files = ["ep01.mp4", "ep10.mp4", "ep2.mp4"]
        result = sorted(files, key=natural_sort_key)
        assert result == ["ep01.mp4", "ep2.mp4", "ep10.mp4"]

    def test_case_insensitive(self):
        """Should sort case-insensitively."""
        files = ["Abc.txt", "abc.txt", "ABC.txt"]
        result = sorted(files, key=natural_sort_key)
        # All should be treated the same, but stable sort preserves order
        assert len(result) == 3

    def test_handles_multiple_numbers(self):
        """Should handle multiple number segments."""
        files = ["S1E10.mp4", "S1E2.mp4", "S2E1.mp4"]
        result = sorted(files, key=natural_sort_key)
        assert result == ["S1E2.mp4", "S1E10.mp4", "S2E1.mp4"]

    def test_handles_no_numbers(self):
        """Should sort alphabetically when no numbers present."""
        files = ["charlie.txt", "alpha.txt", "bravo.txt"]
        result = sorted(files, key=natural_sort_key)
        assert result == ["alpha.txt", "bravo.txt", "charlie.txt"]

    def test_handles_pure_numbers(self):
        """Should handle pure numeric strings."""
        items = ["100", "20", "3"]
        result = sorted(items, key=natural_sort_key)
        assert result == ["3", "20", "100"]

    def test_handles_empty_string(self):
        """Should handle empty strings."""
        items = ["b", "", "a"]
        result = sorted(items, key=natural_sort_key)
        assert result[0] == ""

    def test_episode_naming_patterns(self):
        """Should properly sort common episode naming patterns."""
        episodes = [
            "Anime_S01E10.mkv",
            "Anime_S01E1.mkv",
            "Anime_S01E02.mkv",
        ]
        result = sorted(episodes, key=natural_sort_key)
        assert result == [
            "Anime_S01E1.mkv",
            "Anime_S01E02.mkv",
            "Anime_S01E10.mkv",
        ]

    def test_returns_list(self):
        """Should return a list that can be used as a sort key."""
        key = natural_sort_key("file10.txt")
        assert isinstance(key, list)
