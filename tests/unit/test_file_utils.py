"""Tests for file_utils module."""

import pytest

from anki_miner.utils.file_utils import ensure_directory, safe_filename


class TestEnsureDirectory:
    """Tests for ensure_directory function."""

    def test_creates_directory(self, tmp_path):
        """Should create a new directory."""
        new_dir = tmp_path / "new_folder"
        assert not new_dir.exists()

        result = ensure_directory(new_dir)

        assert new_dir.exists()
        assert new_dir.is_dir()
        assert result == new_dir

    def test_creates_nested_directories(self, tmp_path):
        """Should create nested directories."""
        nested_dir = tmp_path / "level1" / "level2" / "level3"
        assert not nested_dir.exists()

        ensure_directory(nested_dir)

        assert nested_dir.exists()
        assert nested_dir.is_dir()

    def test_existing_directory_ok(self, tmp_path):
        """Should handle already existing directory."""
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        # Should not raise
        result = ensure_directory(existing_dir)

        assert existing_dir.exists()
        assert result == existing_dir


class TestSafeFilename:
    """Tests for safe_filename function."""

    @pytest.mark.parametrize(
        "input_str, expected",
        [
            ("file<name>.txt", "file_name_.txt"),
            ("file:name.txt", "file_name.txt"),
            ('file"name".txt', "file_name_.txt"),
            ("file/name\\path.txt", "file_name_path.txt"),
            ("file|name.txt", "file_name.txt"),
            ("file?name.txt", "file_name.txt"),
            ("file*name.txt", "file_name.txt"),
            # `[`/`]` terminate Anki's `[sound:...]` tag, so they must be
            # sanitized out of media filenames (7.5 / Yomitan backend `]` strip).
            ("word[reading].mp3", "word_reading_.mp3"),
            ('<>:"/\\|?*[]', "___________"),
            ("", "unnamed"),
        ],
        ids=[
            "angle_brackets",
            "colon",
            "quotes",
            "slashes",
            "pipe",
            "question_mark",
            "asterisk",
            "square_brackets",
            "all_invalid",
            "empty_string",
        ],
    )
    def test_replaces_unsafe_characters(self, input_str, expected):
        """Should replace unsafe filesystem characters with underscore."""
        assert safe_filename(input_str) == expected

    def test_preserves_safe_characters(self):
        """Should preserve safe characters."""
        safe_name = "valid_filename-123.txt"
        assert safe_filename(safe_name) == safe_name

    def test_japanese_characters_preserved(self):
        """Should preserve Japanese characters."""
        assert safe_filename("日本語ファイル.txt") == "日本語ファイル.txt"

    @pytest.mark.parametrize(
        "reserved",
        ["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"],
        ids=["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"],
    )
    def test_windows_reserved_names_prefixed(self, reserved):
        """Windows reserved names should get an underscore prefix."""
        result = safe_filename(f"{reserved}.txt")
        assert result == f"_{reserved}.txt"

    def test_windows_reserved_names_case_insensitive(self):
        """Reserved name check should be case-insensitive."""
        result = safe_filename("con.txt")
        assert result == "_con.txt"

    def test_truncates_long_filename_to_255_bytes(self):
        """Filenames exceeding 255 UTF-8 bytes should be truncated."""
        # Each Japanese char is 3 bytes in UTF-8, so 90 chars = 270 bytes + ".txt" = 274 bytes
        long_name = "あ" * 90 + ".txt"
        result = safe_filename(long_name)
        assert len(result.encode("utf-8")) <= 255
        assert result.endswith(".txt")

    def test_truncates_preserves_extension(self):
        """Truncation should preserve the file extension."""
        long_name = "x" * 260 + ".mp3"
        result = safe_filename(long_name)
        assert len(result.encode("utf-8")) <= 255
        assert result.endswith(".mp3")
