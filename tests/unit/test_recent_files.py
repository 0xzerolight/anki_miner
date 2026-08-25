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

    def test_add_entry_stores_subtitle_offset(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"), subtitle_offset=2.5)

        entries = manager.get_recent()
        assert entries[0]["subtitle_offset"] == 2.5

    def test_add_entry_defaults_offset_to_zero(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        entries = manager.get_recent()
        assert entries[0]["subtitle_offset"] == 0.0

    def test_legacy_entry_without_offset_still_loads(self, manager):
        """An entry persisted before the offset field existed must still load."""
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        manager._file_path.write_text(
            '[{"video": "/v.mkv", "subtitle": "/s.ass", "timestamp": "2026-01-01T00:00:00+00:00"}]',
            encoding="utf-8",
        )

        entries = manager.get_recent()
        assert len(entries) == 1
        assert entries[0]["video"] == "/v.mkv"

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

    def test_oversized_recent_file_degrades_to_empty(self, manager):
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        manager._file_path.write_text(
            '[{"video":"' + ("x" * 300_000) + '","subtitle":"sub.srt","timestamp":"now"}]',
            encoding="utf-8",
        )

        assert manager.get_recent() == []

    def test_malformed_recent_entry_degrades_to_empty(self, manager):
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        manager._file_path.write_text('[{"video": 1, "subtitle": null}]', encoding="utf-8")

        assert manager.get_recent() == []

    def test_loaded_entries_keep_count_cap(self, manager):
        manager._file_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"video": f"v-{index}", "subtitle": f"s-{index}", "subtitle_offset": 0.0, "timestamp": "now"}
            for index in range(20)
        ]
        import json

        manager._file_path.write_text(json.dumps(entries), encoding="utf-8")

        assert len(manager.get_recent()) == 5

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


class TestAtomicSave:
    """recent_files.json is written atomically and save failures are logged (T-32)."""

    @pytest.fixture
    def manager(self, tmp_path):
        mgr = RecentFilesManager(max_items=5)
        mgr._file_path = tmp_path / "recent_files.json"
        return mgr

    def test_no_temp_left_after_successful_save(self, manager):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        assert manager._file_path.exists()
        tmp = manager._file_path.with_suffix(manager._file_path.suffix + ".tmp")
        assert not tmp.exists()

    def test_replace_failure_logs_warning_and_keeps_old_file(self, manager, monkeypatch, caplog):
        """If os.replace fails mid-save, the previous file is intact and we warn.

        Previously the OSError was swallowed with a bare ``except OSError: pass``
        — the user got no signal that their recent list stopped persisting.
        """
        # Establish a known-good on-disk file first.
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        original = manager._file_path.read_text(encoding="utf-8")

        # _save stages through atomic_write_path, which does the actual
        # os.replace; patch it there rather than importing a now-unused `os`
        # name into recent_files just to give this test something to patch.
        import anki_miner.utils.atomic_io as atomic_io

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(atomic_io.os, "replace", boom)

        with caplog.at_level("WARNING"):
            # add_entry calls _save internally; must not raise.
            manager.add_entry(Path("/video/ep02.mkv"), Path("/subs/ep02.ass"))

        # Old file untouched; no orphan temp.
        assert manager._file_path.read_text(encoding="utf-8") == original
        tmp = manager._file_path.with_suffix(manager._file_path.suffix + ".tmp")
        assert not tmp.exists()
        assert any("recent" in r.message.lower() for r in caplog.records)

    def test_staging_file_name_is_unique_not_the_shared_fixed_tmp(self, manager, monkeypatch):
        """The single-instance guard is advisory, so two processes can race a
        save. A shared, fixed-name ``.tmp`` staging file lets their writes
        interleave and byte-splice a corrupt primary; a unique name per save
        closes that race."""
        import anki_miner.gui.utils.recent_files as rf

        captured: list[str] = []
        real_write_text = rf.Path.write_text

        def spying_write_text(self, *args, **kwargs):
            captured.append(self.name)
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(rf.Path, "write_text", spying_write_text)

        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        manager.add_entry(Path("/video/ep02.mkv"), Path("/subs/ep02.ass"))

        assert len(captured) == 2
        fixed_name = manager._file_path.name + ".tmp"
        assert captured[0] != fixed_name
        assert captured[1] != fixed_name
        assert captured[0] != captured[1]

    def test_no_leftover_sibling_of_any_name_after_successful_save(self, manager):
        """Generalizes test_no_temp_left_after_successful_save: no stray
        staging file of ANY name (not just the old fixed ``.tmp``) survives."""
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))

        assert manager._file_path.exists()
        leftovers = [p for p in manager._file_path.parent.iterdir() if p != manager._file_path]
        assert leftovers == []

    def test_dump_failure_leaves_previous_file_intact_no_litter_of_any_name(self, manager, monkeypatch):
        manager.add_entry(Path("/video/ep01.mkv"), Path("/subs/ep01.ass"))
        original = manager._file_path.read_bytes()

        import anki_miner.gui.utils.recent_files as rf

        def exploding_dumps(*args, **kwargs):
            raise ValueError("boom mid-serialize")

        monkeypatch.setattr(rf.json, "dumps", exploding_dumps)

        with pytest.raises(ValueError):
            manager.add_entry(Path("/video/ep02.mkv"), Path("/subs/ep02.ass"))

        assert manager._file_path.read_bytes() == original
        leftovers = [p for p in manager._file_path.parent.iterdir() if p != manager._file_path]
        assert leftovers == []
