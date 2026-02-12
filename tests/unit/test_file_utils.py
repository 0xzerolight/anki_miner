"""Tests for file_utils module."""

import pytest

from anki_miner.utils.file_utils import cleanup_temp_files, ensure_directory, safe_filename


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


class TestCleanupTempFiles:
    """Tests for cleanup_temp_files function."""

    def test_removes_matching_files(self, tmp_path):
        """Should remove files matching pattern."""
        # Create test files
        (tmp_path / "test1.tmp").write_text("temp")
        (tmp_path / "test2.tmp").write_text("temp")
        (tmp_path / "keep.txt").write_text("keep")

        count = cleanup_temp_files(tmp_path, "*.tmp")

        assert count == 2
        assert not (tmp_path / "test1.tmp").exists()
        assert not (tmp_path / "test2.tmp").exists()
        assert (tmp_path / "keep.txt").exists()

    def test_removes_all_files_with_default_pattern(self, tmp_path):
        """Should remove all files with default pattern."""
        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.txt").write_text("b")

        count = cleanup_temp_files(tmp_path)

        assert count == 2

    def test_returns_zero_for_nonexistent_directory(self, tmp_path):
        """Should return 0 for non-existent directory."""
        nonexistent = tmp_path / "does_not_exist"

        count = cleanup_temp_files(nonexistent)

        assert count == 0

    def test_ignores_subdirectories(self, tmp_path):
        """Should not remove subdirectories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "file.txt").write_text("temp")

        count = cleanup_temp_files(tmp_path)

        assert count == 1
        assert subdir.exists()

    def test_returns_zero_for_empty_directory(self, tmp_path):
        """Should return 0 for empty directory."""
        count = cleanup_temp_files(tmp_path, "*.tmp")
        assert count == 0


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
            ('<>:"/\\|?*', "_________"),
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
