"""Tests for the Yomitan zip importer."""

from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.importers.yomitan_importer import (
    YomitanImportResult,
    derive_dict_id_from_zip,
    import_yomitan_zip,
)
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, open_readonly, read_meta
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
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("食べる",)).fetchone()[0]
            assert "to eat" in content
            # Tag badges moved to provider-side composition (Task 4); content
            # is now glossary-only items. Task 3 will populate DictRow.tags.
            assert '<li class="gloss-item">' in content
        finally:
            conn.close()

        meta = read_meta(db_path)
        assert meta["schema_version"] == str(SCHEMA_VERSION)
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

    def test_dict_media_extracted_and_referenced_by_namespaced_filename(self, tmp_path: Path):
        """Yomitan zips for monolingual dicts ship SVG/PNG assets referenced
        from structured content. The importer must copy those into the dict's
        media folder and rewrite each `<img src>` to the flat namespaced
        filename that AnkiConnect can later serve.
        """
        svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "test.zip",
            term_banks=[
                [
                    [
                        "走る",
                        "はしる",
                        "v5r",
                        "",
                        0,
                        [
                            {
                                "type": "structured-content",
                                "content": {
                                    "tag": "span",
                                    "content": [
                                        "はし",
                                        {"tag": "img", "path": "svg/accent.svg"},
                                        "る",
                                    ],
                                },
                            }
                        ],
                        1,
                        "",
                    ],
                ]
            ],
            media_files={"svg/accent.svg": svg_bytes},
        )

        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)

        # Asset copied flat under the dict folder using the safe-basename form.
        media_dir = dest_root / result.dict_id / "media"
        assert media_dir.is_dir()
        copied = media_dir / "svg_accent.svg"
        assert copied.exists()
        assert copied.read_bytes() == svg_bytes

        # Stored HTML references the namespaced flat filename — not the
        # original zip-relative path.
        db_path = dest_root / result.dict_id / "index.sqlite"
        conn = open_readonly(db_path)
        try:
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("走る",)).fetchone()[0]
        finally:
            conn.close()

        expected_src = f'src="{result.dict_id}__svg_accent.svg"'
        assert expected_src in content
        # Renderer now emits the envelope; class is space-joined with the
        # `gloss-image` marker but `anki-miner-dict-media` still rides along
        # so AnkiService._DICT_MEDIA_IMG_RE picks it up.
        assert "anki-miner-dict-media" in content
        assert 'class="gloss-image anki-miner-dict-media"' in content
        # The dict-internal path must NOT leak into Anki via src; it does
        # however now appear in the envelope's `data-path` for round-tripping.
        assert 'src="svg/accent.svg"' not in content

    def test_no_media_folder_when_no_assets_referenced(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "plain.zip")
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        media_dir = dest_root / result.dict_id / "media"
        assert not media_dir.exists()

    def test_glossary_rendered_into_content(self, tmp_path: Path):
        """Importer must store the rendered glossary HTML for the term's senses.

        Tag badges are now provider-side composition (Task 4) and no longer
        appear in `content`. `DictRow.tags` (Task 3) carries the merged tag
        list; this test only guards that glossary text survives the new
        renderer.
        """
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
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("走る",)).fetchone()[0]
            assert '<li class="gloss-item">' in content
            assert "to run" in content
            # Renderer no longer emits tag badges or tag-list wrapper.
            assert "tag-list" not in content
            assert 'class="tag ' not in content
        finally:
            conn.close()

    def test_tags_column_populated_from_definition_and_term_tags(self, tmp_path: Path):
        """`DictRow.tags` is the union of term-bank column 3 (definitionTags)
        and column 8 (termTags), space-joined, preserving order.
        """
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "test.zip",
            term_banks=[
                [
                    # definitionTags="v5r vt", termTags="common P"
                    ["走る", "はしる", "v5r vt", "v5r", 0, ["to run"], 1, "common P"],
                ]
            ],
            tag_banks=[],  # no tag_bank_*.json files at all
        )
        result = import_yomitan_zip(zip_path, tmp_path / "dicts")

        db_path = tmp_path / "dicts" / result.dict_id / "index.sqlite"
        conn = open_readonly(db_path)
        try:
            tags = conn.execute("SELECT tags FROM entries WHERE term = ?", ("走る",)).fetchone()[0]
        finally:
            conn.close()

        # definitionTags first, then termTags, order preserved within each.
        assert tags == "v5r vt common P"

    def test_tags_column_empty_when_no_tag_columns(self, tmp_path: Path):
        """When both definitionTags and termTags are empty strings, `tags=""`."""
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "test.zip",
            term_banks=[
                [
                    ["走る", "はしる", "", "v5r", 0, ["to run"], 1, ""],
                ]
            ],
            tag_banks=[],
        )
        result = import_yomitan_zip(zip_path, tmp_path / "dicts")

        db_path = tmp_path / "dicts" / result.dict_id / "index.sqlite"
        conn = open_readonly(db_path)
        try:
            tags = conn.execute("SELECT tags FROM entries WHERE term = ?", ("走る",)).fetchone()[0]
        finally:
            conn.close()

        assert tags == ""

    def test_import_succeeds_without_tag_bank_files(self, tmp_path: Path):
        """A zip with zero `tag_bank_*.json` files must still import cleanly.

        Provider-side composition reads tags directly off `DictRow.tags`, so
        the importer no longer requires the legacy tag-bank descriptor files.
        """
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "test.zip",
            term_banks=[
                [
                    ["食べる", "たべる", "v1", "v1", 0, ["to eat"], 1, ""],
                ]
            ],
            tag_banks=[],  # importer must not require tag_bank_*.json
        )
        # Sanity: the fixture really does omit tag-bank files.
        import zipfile

        with zipfile.ZipFile(zip_path, "r") as zf:
            assert not any(n.startswith("tag_bank_") for n in zf.namelist())

        result = import_yomitan_zip(zip_path, tmp_path / "dicts")
        assert result.entry_count == 1


class TestDeriveDictIdFromZip:
    """The shared `derive_dict_id_from_zip` helper used by the Settings UI."""

    def test_matches_importer_dict_id(self, tmp_path: Path):
        """Helper output must equal the importer's `dict_id` for the same zip."""
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip", title="My Dict", revision="2024-01")
        derived = derive_dict_id_from_zip(zip_path)
        imported = import_yomitan_zip(zip_path, tmp_path / "dicts").dict_id
        assert derived == imported

    def test_omits_revision_suffix_when_blank(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "norev.zip", title="NoRev", revision="")
        assert derive_dict_id_from_zip(zip_path) == "norev"

    def test_raises_when_zip_missing(self, tmp_path: Path):
        with pytest.raises(SetupError, match="not found"):
            derive_dict_id_from_zip(tmp_path / "missing.zip")

    def test_raises_on_missing_index_json(self, tmp_path: Path):
        import zipfile

        bad = tmp_path / "noindex.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("term_bank_1.json", "[]")
        with pytest.raises(SetupError, match="missing required index.json"):
            derive_dict_id_from_zip(bad)

    def test_raises_on_blank_title(self, tmp_path: Path):
        import json
        import zipfile

        bad = tmp_path / "blank.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("index.json", json.dumps({"title": "", "revision": "v1", "format": 3}))
        with pytest.raises(SetupError, match="missing required 'title'"):
            derive_dict_id_from_zip(bad)

    def test_raises_on_corrupt_zip(self, tmp_path: Path):
        bad = tmp_path / "corrupt.zip"
        bad.write_bytes(b"this is not a zip file")
        with pytest.raises(SetupError, match="Corrupt zip"):
            derive_dict_id_from_zip(bad)
