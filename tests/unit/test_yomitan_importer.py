"""Tests for the Yomitan zip importer."""

from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.importers.yomitan_importer import (
    YomitanImportResult,
    import_yomitan_zip,
)
from anki_miner.services.dictionary.storage import open_readonly, read_meta
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip


class TestImportYomitanZip:
    def test_import_creates_sqlite_with_expected_rows(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"

        result = import_yomitan_zip(zip_path, dest_root)

        assert isinstance(result, YomitanImportResult)
        assert result.dict_id.startswith("test-dict")
        assert result.entry_count == 2

        db_path = dest_root / result.dict_id / "index.sqlite"
        assert db_path.exists()

        conn = open_readonly(db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            assert count == 2
            content = conn.execute(
                "SELECT content FROM entries WHERE term = ?", ("食べる",)
            ).fetchone()[0]
            assert "to eat" in content
            assert '<span class="tag tag-expression">v1</span>' in content
        finally:
            conn.close()

        meta = read_meta(db_path)
        assert meta["schema_version"] == "1"
        assert meta["format"] == "yomitan"
        assert meta["source_name"] == "Test Dict"
        assert meta["source_revision"] == "v1"
        assert meta["entry_count"] == "2"

    def test_progress_callback_fires(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"
        events: list[tuple[int, int, str]] = []

        import_yomitan_zip(
            zip_path,
            dest_root,
            progress=lambda cur, total, msg: events.append((cur, total, msg)),
        )

        assert events  # at least one progress event
        final_cur, final_total, _ = events[-1]
        assert final_cur == final_total

    def test_rejects_old_format_version(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "old.zip", format_version=2)
        with pytest.raises(SetupError, match="Unsupported Yomitan format"):
            import_yomitan_zip(zip_path, tmp_path / "dicts")

    def test_rejects_missing_index_json(self, tmp_path: Path):
        import zipfile

        zip_path = tmp_path / "bad.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("term_bank_1.json", "[]")
        with pytest.raises(SetupError, match="missing required index.json"):
            import_yomitan_zip(zip_path, tmp_path / "dicts")

    def test_overwrite_disabled_raises_when_dir_exists(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"

        import_yomitan_zip(zip_path, dest_root)

        with pytest.raises(SetupError, match="already exists"):
            import_yomitan_zip(zip_path, dest_root, overwrite=False)

    def test_overwrite_enabled_replaces_existing(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"
        first = import_yomitan_zip(zip_path, dest_root)
        second = import_yomitan_zip(zip_path, dest_root, overwrite=True)
        assert second.dict_id == first.dict_id
        assert (dest_root / first.dict_id / "index.sqlite").exists()

    @pytest.mark.parametrize(
        "evil_name",
        [
            "../../../escape.json",  # POSIX traversal
            "..\\..\\escape.json",  # Windows backslash traversal
            "/absolute/escape.json",  # Absolute path
            "C:\\Windows\\escape.json",  # Windows drive letter
            "subdir/../../escape.json",  # Indirect traversal
        ],
    )
    def test_rejects_zip_with_unsafe_paths(self, tmp_path: Path, evil_name: str):
        import zipfile

        bad = tmp_path / "evil.zip"
        bad.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(evil_name, "{}")
            zf.writestr("index.json", '{"title": "x", "revision": "v1", "format": 3}')

        with pytest.raises(SetupError, match="unsafe|escaping"):
            import_yomitan_zip(bad, tmp_path / "dicts")

    def test_cancel_check_aborts_import(self, tmp_path: Path):
        """cancel_check returning True must raise SetupError and leave dest_root untouched."""
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"
        calls = [0]

        def cancel_check() -> bool:
            calls[0] += 1
            return calls[0] >= 1  # cancel on the very first check

        with pytest.raises(SetupError, match="cancelled"):
            import_yomitan_zip(zip_path, dest_root, cancel_check=cancel_check)

        # dest_root must not contain a partial dict folder
        assert not any(dest_root.iterdir()) if dest_root.exists() else True

    def test_merges_definition_tags_and_term_tags(self, tmp_path: Path):
        """Yomitan term-bank entries have tags at both entry[2] and entry[7]; both must render."""
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "test.zip",
            term_banks=[
                [
                    ["走る", "はしる", "v5r", "v5r", 0, ["to run"], 1, "common"],
                ]
            ],
            tag_banks=[
                [
                    ["v5r", "expression", -3, "Godan verb -ru", 0],
                    ["common", "frequency", 0, "Common word", 0],
                ]
            ],
        )
        result = import_yomitan_zip(zip_path, tmp_path / "dicts")

        db_path = tmp_path / "dicts" / result.dict_id / "index.sqlite"
        conn = open_readonly(db_path)
        try:
            content = conn.execute(
                "SELECT content FROM entries WHERE term = ?", ("走る",)
            ).fetchone()[0]
            # Both tag sources must appear with their categories
            assert '<span class="tag tag-expression">v5r</span>' in content
            assert '<span class="tag tag-frequency">common</span>' in content
        finally:
            conn.close()
