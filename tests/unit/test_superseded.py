"""Tests for the date-versioned duplicate sweep (services/dictionary/superseded)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.dictionary.storage import create_index, write_meta
from anki_miner.services.dictionary.superseded import (
    strip_date_bracket,
    sweep_superseded_dicts,
)


def _seed(dicts_root: Path, dict_id: str, source_name: str) -> Path:
    db = dicts_root / dict_id / "index.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    create_index(db)
    write_meta(db, {"source_name": source_name})
    return db.parent


class TestStripDateBracket:
    def test_strips_trailing_iso_date(self):
        assert strip_date_bracket("Jitendex.org [2026-06-06]") == ("Jitendex.org", True)

    def test_no_bracket_is_untouched(self):
        assert strip_date_bracket("Daijirin") == ("Daijirin", False)

    def test_non_date_bracket_is_not_stripped(self):
        # Variant/language tags must NOT be treated as a date.
        assert strip_date_bracket("Some Dict [Names]") == ("Some Dict [Names]", False)


class TestSweepSupersededDicts:
    def test_removes_same_base_date_versioned_copy(self, tmp_path: Path):
        dicts = tmp_path / "dicts"
        _seed(dicts, "jitendex", "Jitendex.org [2026-06-06]")
        _seed(dicts, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        assert swept == [("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")]
        assert failed == []
        assert not (dicts / "jitendex-org-2025-11-05").exists()
        assert (dicts / "jitendex").exists()  # keep_id never removed

    def test_removes_two_legacy_copies(self, tmp_path: Path):
        dicts = tmp_path / "dicts"
        _seed(dicts, "jitendex", "Jitendex.org [2026-06-06]")
        _seed(dicts, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")
        _seed(dicts, "jitendex-org-2025-10-05", "Jitendex.org [2025-10-05]")

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        assert {i for i, _ in swept} == {"jitendex-org-2025-11-05", "jitendex-org-2025-10-05"}
        assert failed == []

    def test_noop_when_imported_name_has_no_date_bracket(self, tmp_path: Path):
        dicts = tmp_path / "dicts"
        _seed(dicts, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")

        swept, failed = sweep_superseded_dicts(dicts, keep_id="jitendex", imported_source_name="Jitendex.org")

        assert swept == [] and failed == []
        assert (dicts / "jitendex-org-2025-11-05").exists()

    def test_does_not_touch_different_base(self, tmp_path: Path):
        dicts = tmp_path / "dicts"
        _seed(dicts, "daijirin-2025-01-01", "Daijirin [2025-01-01]")

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        assert swept == [] and failed == []
        assert (dicts / "daijirin-2025-01-01").exists()

    def test_does_not_touch_same_base_without_bracket(self, tmp_path: Path):
        # A user-curated bracket-less copy sharing the base must survive.
        dicts = tmp_path / "dicts"
        _seed(dicts, "my-jitendex", "Jitendex.org")

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        assert swept == [] and failed == []
        assert (dicts / "my-jitendex").exists()

    def test_survives_corrupt_sibling_and_still_sweeps(self, tmp_path: Path):
        dicts = tmp_path / "dicts"
        _seed(dicts, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")
        # Corrupt/foreign index.sqlite (no meta table) → read_meta raises
        # sqlite3.Error, which must be swallowed per-sibling, not abort the loop.
        corrupt = dicts / "broken" / "index.sqlite"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not a database")

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        assert swept == [("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")]
        assert failed == []
        assert (dicts / "broken").exists()

    def test_rmtree_failure_is_reported_not_raised(self, tmp_path: Path, monkeypatch):
        dicts = tmp_path / "dicts"
        _seed(dicts, "jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")

        import anki_miner.services.dictionary.superseded as mod

        def boom(_path):
            raise OSError("Access denied")

        monkeypatch.setattr(mod.shutil, "rmtree", boom)

        swept, failed = sweep_superseded_dicts(
            dicts, keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )

        # No orphan: the id is reported as failed (chain entry kept), NOT swept,
        # and the directory remains on disk.
        assert swept == []
        assert failed == [("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")]
        assert (dicts / "jitendex-org-2025-11-05").exists()

    def test_missing_dicts_root_is_noop(self, tmp_path: Path):
        swept, failed = sweep_superseded_dicts(
            tmp_path / "nope", keep_id="jitendex", imported_source_name="Jitendex.org [2026-06-06]"
        )
        assert swept == [] and failed == []
