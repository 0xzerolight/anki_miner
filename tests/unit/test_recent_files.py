"""Tests for RecentFilesManager."""

from pathlib import Path

import pytest

from anki_miner.gui.utils.recent_files import RecentFilesManager


class TestRecentFilesManager:
    """Tests for RecentFilesManager."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a RecentFilesManager with a temp file path."""
        mgr = RecentFilesManager(max_items=5)
        # Override the file path to use tmp_path
        mgr._file_path = tmp_path / "recent_files.json"
        return mgr

    def test_get_recent_empty_initially(self, manager):
        assert manager.get_recent() == []

    def test_add_entry_creates_file(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        assert manager._file_path.exists()

    def test_add_entry_stores_data(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        entries = manager.get_recent()
        assert len(entries) == 1
        assert entries[0]["video"] == "/video/ep01.mkv"
        assert entries[0]["subtitle"] == "/subs/ep01.ass"
        assert "timestamp" in entries[0]

    def test_most_recent_first(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        manager.add_entry(Path("/video/ep02.mkv"), Path("/subs/ep02.ass"))

        entries = manager.get_recent()
        assert len(entries) == 2
        assert entries[0]["video"] == "/video/ep02.mkv"
        assert entries[1]["video"] == "/video/ep01.mkv"

    def test_max_items_enforced(self, manager):
        for i in range(10):
            manager.add_entry(Path(f"/video/ep{i:02d}.mkv"), Path(f"/subs/ep{i:02d}.ass"))

        entries = manager.get_recent()
        assert len(entries) == 5  # max_items=5

    def test_deduplication(self, manager):
        """Adding the same pair twice should keep only the most recent."""
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        manager.add_entry(Path("/video/ep02.mkv"), Path("/subs/ep02.ass"))
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))  # duplicate

        entries = manager.get_recent()
        assert len(entries) == 2
        # ep01 should be first (most recent)
        assert entries[0]["video"] == "/video/ep01.mkv"
        assert entries[1]["video"] == "/video/ep02.mkv"

    def test_clear(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        manager.clear()

        assert manager.get_recent() == []
        assert not manager._file_path.exists()

    def test_clear_when_no_file(self, manager):
        """Clear should not fail if the file doesn't exist."""
        manager.clear()
        assert manager.get_recent() == []

    def test_handles_corrupt_json(self, manager):
        """Should return empty list if JSON is corrupt."""
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        manager._file_path.write_text("not valid json", encoding="utf-8")

        entries = manager.get_recent()
        assert entries == []

    def test_handles_non_list_json(self, manager):
        """Should return empty list if JSON is not a list."""
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        manager._file_path.write_text('{"key": "value"}', encoding="utf-8")

        entries = manager.get_recent()
        assert entries == []

    def test_timestamp_is_iso_format(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        entries = manager.get_recent()
        timestamp = entries[0]["timestamp"]
        # Should parse without error
        from datetime import datetime

        datetime.fromisoformat(timestamp)

    def test_creates_parent_directory(self, tmp_path):
        """Should create ~/.anki_miner/ if it doesn't exist."""
        mgr = RecentFilesManager()
        mgr._file_path = tmp_path / "nested" / "dir" / "recent_files.json"

        mgr.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        assert mgr._file_path.exists()

    def test_preserves_entries_across_instances(self, tmp_path):
        """Entries should persist across manager instances."""
        file_path = tmp_path / "recent_files.json"

        mgr1 = RecentFilesManager()
        mgr1._file_path = file_path
        mgr1.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        mgr2 = RecentFilesManager()
        mgr2._file_path = file_path

        entries = mgr2.get_recent()
        assert len(entries) == 1
        assert entries[0]["video"] == "/video/ep01.mkv"
