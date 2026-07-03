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

    def test_import_survives_lone_surrogate_in_glossary(self, tmp_path: Path):
        """Issue #67: a hand-converted dict with a lone UTF-16 surrogate in a
        glossary must import (scrubbed to U+FFFD) instead of crashing with
        'utf-8 codec can't encode character ... surrogates not allowed'.

        json.dumps writes '\\ud867' as an escape; the importer's json.loads
        reproduces the lone surrogate — the exact production path."""
        term_banks = [[["危険", "きけん", "", "", 0, ["danger\ud867ous"], 1, ""]]]
        zip_path = build_yomitan_zip(tmp_path / "src" / "bad.zip", term_banks=term_banks)
        dest_root = tmp_path / "dicts"

        result = import_yomitan_zip(zip_path, dest_root)
        assert result.entry_count == 1

        db_path = dest_root / result.dict_id / "index.sqlite"
        conn = open_readonly(db_path)
        try:
            content = conn.execute("SELECT content FROM entries WHERE term = ?", ("危険",)).fetchone()[0]
            assert "danger�ous" in content
            assert "\ud867" not in content
        finally:
            conn.close()

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

    def test_import_creates_source_zip(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"

        result = import_yomitan_zip(zip_path, dest_root)

        saved = dest_root / result.dict_id / "source.zip"
        assert saved.exists()
        assert saved.read_bytes() == zip_path.read_bytes()

    def test_reimport_seeds_source_zip_for_legacy_dict(self, tmp_path: Path):
        """Pre-existing dict folder (index.sqlite, no source.zip) gains a
        source.zip after the per-row reimport flow (overwrite=True). This is
        the path users hit when reimporting a dict installed before the
        source-copy feature shipped.
        """
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"

        # First import seeds the dict; remove source.zip to simulate legacy.
        first = import_yomitan_zip(zip_path, dest_root)
        legacy_source = dest_root / first.dict_id / "source.zip"
        legacy_source.unlink()
        assert not legacy_source.exists()

        # Per-row reimport path calls the importer with overwrite=True.
        import_yomitan_zip(zip_path, dest_root, overwrite=True)
        assert legacy_source.exists()
        assert legacy_source.read_bytes() == zip_path.read_bytes()

    def test_reimport_replaces_source_zip(self, tmp_path: Path):
        first_zip = build_yomitan_zip(tmp_path / "src" / "first.zip")
        # Different term_banks ⇒ different bytes, same dict_id (title/revision unchanged)
        second_zip = build_yomitan_zip(
            tmp_path / "src" / "second.zip",
            term_banks=[
                [
                    ["食べる", "たべる", "v1", "v1", 0, ["to eat", "to consume"], 1, ""],
                    ["飲む", "のむ", "v5m", "v5m", 0, ["to drink"], 2, ""],
                    ["走る", "はしる", "v5r", "v5r", 0, ["to run"], 3, ""],
                ]
            ],
        )
        dest_root = tmp_path / "dicts"

        first = import_yomitan_zip(first_zip, dest_root)
        import_yomitan_zip(second_zip, dest_root, overwrite=True)

        saved = dest_root / first.dict_id / "source.zip"
        assert saved.read_bytes() == second_zip.read_bytes()
        # No .bak-* folder remains after successful overwrite
        backups = [p for p in dest_root.iterdir() if p.name.startswith(first.dict_id + ".bak-")]
        assert backups == []

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

    def test_duplicate_import_fails_before_any_staging_work(self, tmp_path: Path):
        """4.7a: a re-add of an existing dict (overwrite=False) must fail right
        after deriving dict_id — before any per-file rendering/progress."""
        zip_path = build_yomitan_zip(tmp_path / "src" / "test.zip")
        dest_root = tmp_path / "dicts"
        import_yomitan_zip(zip_path, dest_root)

        events: list[tuple[int, int, str]] = []
        with pytest.raises(SetupError, match="already exists"):
            import_yomitan_zip(
                zip_path,
                dest_root,
                progress=lambda c, t, m: events.append((c, t, m)),
            )
        # No staging/render work happened: the progress callback never fired.
        assert events == []

    def test_nested_index_json_raises_rezip_diagnostic(self, tmp_path: Path):
        """4.7b: a zip whose index.json is nested under a redundant directory
        (user zipped the folder, not its contents) gets a guiding error."""
        import zipfile

        zip_path = tmp_path / "nested.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("MyDict/index.json", '{"title": "x", "revision": "v1", "format": 3}')
            zf.writestr("MyDict/term_bank_1.json", "[]")
        with pytest.raises(SetupError, match="re-zip the folder CONTENTS"):
            import_yomitan_zip(zip_path, tmp_path / "dicts")

    def test_malformed_term_entries_counted_and_surfaced(self, tmp_path: Path):
        """4.8: structurally-bad entries are skipped-with-a-count, not silently
        dropped, so a drastically-reduced import is visible."""
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "m.zip",
            term_banks=[
                [
                    ["食べる", "たべる", "v1", "v1", 0, ["to eat"], 1, ""],  # valid
                    ["飲む", "のむ"],  # arity 2 < 6 → malformed
                    ["", "", "", "", 0, ["x"]],  # blank term → malformed
                    "not-a-list",  # not a list → malformed
                ]
            ],
        )
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        assert result.entry_count == 1
        assert result.skipped_malformed == 3

    def test_non_array_term_bank_raises_naming_file(self, tmp_path: Path):
        """4.8: a term bank whose top-level JSON is not an array is wholly
        unreadable and raises, naming the offending file."""
        import zipfile

        zip_path = tmp_path / "bad.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", '{"title": "x", "revision": "v1", "format": 3}')
            zf.writestr("term_bank_1.json", '{"oops": "object not array"}')
        with pytest.raises(SetupError, match="term_bank_1.json"):
            import_yomitan_zip(zip_path, tmp_path / "dicts")

    def test_media_unsupported_extension_warned_not_copied(self, tmp_path: Path):
        """4.7c: a referenced asset with a non-image extension is skipped with a
        context-rich warning instead of copied blindly into Anki's media store."""
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "m.zip",
            term_banks=[
                [
                    [
                        "走る",
                        "はしる",
                        "v5r",
                        "",
                        0,
                        [{"type": "structured-content", "content": {"tag": "img", "path": "assets/note.txt"}}],
                        1,
                        "",
                    ]
                ]
            ],
            media_files={"assets/note.txt": b"hello"},
        )
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)

        assert any("note.txt" in w and "unsupported media type" in w for w in result.media_warnings)
        assert not (dest_root / result.dict_id / "media" / "assets_note.txt").exists()

    def test_media_undecodable_image_warned_not_copied(self, tmp_path: Path):
        """4.7c: a referenced .png that is not actually a valid image fails the
        Pillow decode probe and is warned about, not copied."""
        pytest.importorskip("PIL")
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "m.zip",
            term_banks=[
                [
                    [
                        "走る",
                        "はしる",
                        "v5r",
                        "",
                        0,
                        [{"type": "structured-content", "content": {"tag": "img", "path": "img/broken.png"}}],
                        1,
                        "",
                    ]
                ]
            ],
            media_files={"img/broken.png": b"not a real png"},
        )
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)

        assert any("broken.png" in w and "decode" in w for w in result.media_warnings)
        assert not (dest_root / result.dict_id / "media" / "img_broken.png").exists()

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

    def test_raises_rezip_diagnostic_on_nested_index(self, tmp_path: Path):
        """4.7b: derive path must also surface the redundant-directory hint."""
        import zipfile

        nested = tmp_path / "nested.zip"
        with zipfile.ZipFile(nested, "w") as zf:
            zf.writestr("Sub/index.json", '{"title": "x", "revision": "v1", "format": 3}')
        with pytest.raises(SetupError, match="re-zip the folder CONTENTS"):
            derive_dict_id_from_zip(nested)

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

    def test_oversized_index_json_rejected_without_full_read(self, tmp_path: Path):
        """T-35: a small zip carrying a huge highly-compressible index.json must
        be rejected on its DECLARED uncompressed size, before the bytes are read
        fully into memory (which would OOM the process when a user picks the zip
        for a reimport slot)."""
        import zipfile
        from unittest.mock import patch

        from anki_miner.services.dictionary.importers import yomitan_importer

        bad = tmp_path / "bomb.zip"
        # Declared uncompressed size just over the cap; compresses to a few KB
        # on disk so the test stays fast and small.
        payload = b" " * (yomitan_importer.MAX_INDEX_JSON_BYTES + 1)
        with zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.json", payload)

        # Fail loudly if the implementation ever does an unbounded read of the
        # entry — proving the declared-size cap (or a bounded read) fires first.
        real_read = zipfile.ZipExtFile.read

        def guard(self, n=-1):  # noqa: ANN001
            if n is None or n < 0:
                raise AssertionError("derive_dict_id_from_zip read index.json without a size cap")
            return real_read(self, n)

        with patch.object(zipfile.ZipExtFile, "read", guard), pytest.raises(SetupError, match="(?i)index.json"):
            derive_dict_id_from_zip(bad)


class TestStylesCssCapture:
    """Issue #87: a dictionary's root styles.css is captured into meta."""

    def test_styles_css_stored_in_meta(self, tmp_path: Path):
        css = 'span[data-sc-class="tag"] { color: red }'
        zip_path = build_yomitan_zip(tmp_path / "src" / "styled.zip", styles_css=css)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        meta = read_meta(dest_root / result.dict_id / "index.sqlite")
        assert meta["styles_css"] == css

    def test_no_styles_css_means_no_meta_key(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "plain.zip")
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        meta = read_meta(dest_root / result.dict_id / "index.sqlite")
        assert "styles_css" not in meta

    def test_oversized_styles_css_skipped(self, tmp_path: Path):
        big = "a{color:red}\n" * 60000  # > 512 KiB
        zip_path = build_yomitan_zip(tmp_path / "src" / "big.zip", styles_css=big)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        meta = read_meta(dest_root / result.dict_id / "index.sqlite")
        assert "styles_css" not in meta


class TestTagBankImport:
    """schema v3: tag_bank_*.json + legacy index.json tagMeta → tags table."""

    def test_tag_bank_written_to_tags_table(self, tmp_path: Path):
        # Mirror valid-dictionary1's 15 tags across three banks.
        tag_banks = [
            [
                ["E1", "default", 0, "example tag 1", 0],
                ["E2", "default", 0, "example tag 2", 0],
                ["P", "popular", 0, "popular term", 0],
                ["n", "partOfSpeech", 0, "noun", 0],
                ["vt", "partOfSpeech", 0, "transitive verb", 0],
                ["abbr", "default", 0, "abbreviation", 0],
            ],
            [
                ["K1", "default", 0, "example kanji tag 1", 0],
                ["K2", "default", 0, "example kanji tag 2", 0],
                ["kstat1", "class", 0, "kanji stat 1", 0],
                ["kstat2", "code", 0, "kanji stat 2", 0],
                ["kstat3", "index", 0, "kanji stat 3", 0],
                ["kstat4", "misc", 0, "kanji stat 4", 0],
                ["kstat5", "misc", 0, "kanji stat 5", 0],
            ],
            [
                ["P1", "default", 0, "example pitch tag 1", 0],
                ["P2", "default", 0, "example pitch tag 2", 0],
            ],
        ]
        zip_path = build_yomitan_zip(tmp_path / "src" / "tags.zip", tag_banks=tag_banks)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)

        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            rows = conn.execute("SELECT name, category, ord, notes, score FROM tags").fetchall()
        finally:
            conn.close()
        by_name = {r[0]: r for r in rows}
        assert len(rows) == 15
        assert by_name["n"] == ("n", "partOfSpeech", 0, "noun", 0.0)
        assert by_name["vt"][3] == "transitive verb"

    def test_tag_bank_notes_and_order_preserved(self, tmp_path: Path):
        tag_banks = [[["uk", "usage", -2, "word usually written using kana alone", 5]]]
        zip_path = build_yomitan_zip(tmp_path / "src" / "uk.zip", tag_banks=tag_banks)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            row = conn.execute("SELECT category, ord, notes, score FROM tags WHERE name = ?", ("uk",)).fetchone()
        finally:
            conn.close()
        assert row == ("usage", -2, "word usually written using kana alone", 5.0)

    def test_legacy_index_tag_meta_converted(self, tmp_path: Path):
        """A dict with no tag_bank files but an inline index.json tagMeta."""
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "legacy.zip",
            tag_banks=[],
            index_extra={
                "tagMeta": {
                    "n": {"category": "partOfSpeech", "order": 1, "notes": "noun", "score": 0},
                    "uk": {"category": "usage", "order": -2, "notes": "usually kana", "score": 0},
                }
            },
        )
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            rows = {r[0]: r for r in conn.execute("SELECT name, category, ord, notes FROM tags")}
        finally:
            conn.close()
        assert rows["n"] == ("n", "partOfSpeech", 1, "noun")
        assert rows["uk"] == ("uk", "usage", -2, "usually kana")

    def test_index_tag_meta_overrides_bank_on_name_clash(self, tmp_path: Path):
        zip_path = build_yomitan_zip(
            tmp_path / "src" / "clash.zip",
            tag_banks=[[["n", "bank", 0, "from bank", 0]]],
            index_extra={"tagMeta": {"n": {"category": "index", "order": 9, "notes": "from index", "score": 0}}},
        )
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            row = conn.execute("SELECT category, notes FROM tags WHERE name = ?", ("n",)).fetchone()
        finally:
            conn.close()
        assert row == ("index", "from index")

    def test_no_tags_leaves_empty_table(self, tmp_path: Path):
        zip_path = build_yomitan_zip(tmp_path / "src" / "notags.zip", tag_banks=[])
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        finally:
            conn.close()

    def test_rules_populated_from_entry_column_3(self, tmp_path: Path):
        """entry[3] (ruleIdentifiers) is stored on entries.rules."""
        term_banks = [[["食べる", "たべる", "v1", "v1 vs", 0, ["to eat"], 1, ""]]]
        zip_path = build_yomitan_zip(tmp_path / "src" / "rules.zip", term_banks=term_banks)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            rules = conn.execute("SELECT rules FROM entries WHERE term = ?", ("食べる",)).fetchone()[0]
        finally:
            conn.close()
        assert rules == "v1 vs"

    def test_reading_stored_hiragana_folded(self, tmp_path: Path):
        """A katakana reading is folded to hiragana at import (schema v3)."""
        term_banks = [[["硝子", "ガラス", "n", "", 0, ["glass"], 1, ""]]]
        zip_path = build_yomitan_zip(tmp_path / "src" / "kana.zip", term_banks=term_banks)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        conn = open_readonly(dest_root / result.dict_id / "index.sqlite")
        try:
            reading = conn.execute("SELECT reading FROM entries WHERE term = ?", ("硝子",)).fetchone()[0]
        finally:
            conn.close()
        assert reading == "がらす"


class TestUpdateMetadata:
    """Update check-and-notify metadata read at import (plan 9.2)."""

    def _import(self, tmp_path: Path, index_extra: dict) -> dict:
        zip_path = build_yomitan_zip(tmp_path / "src" / "u.zip", index_extra=index_extra)
        dest_root = tmp_path / "dicts"
        result = import_yomitan_zip(zip_path, dest_root)
        return read_meta(dest_root / result.dict_id / "index.sqlite")

    def test_updatable_metadata_round_trips(self, tmp_path: Path):
        meta = self._import(
            tmp_path,
            {
                "isUpdatable": True,
                "indexUrl": "https://jitendex.org/index.json",
                "downloadUrl": "https://jitendex.org/jitendex.zip",
                "author": "Stephen Kraus",
                "attribution": "CC BY-SA 4.0",
                "description": "A free JMdict-based dictionary.",
            },
        )
        assert meta["is_updatable"] == "true"
        assert meta["index_url"] == "https://jitendex.org/index.json"
        assert meta["download_url"] == "https://jitendex.org/jitendex.zip"
        assert meta["author"] == "Stephen Kraus"
        assert meta["attribution"] == "CC BY-SA 4.0"
        assert meta["description"] == "A free JMdict-based dictionary."

    def test_non_http_urls_are_not_recorded(self, tmp_path: Path):
        meta = self._import(
            tmp_path,
            {
                "isUpdatable": True,
                "indexUrl": "file:///tmp/index.json",
                "downloadUrl": "https://jitendex.org/jitendex.zip",
            },
        )
        assert "is_updatable" not in meta
        assert "index_url" not in meta
        assert "download_url" not in meta

    def test_isupdatable_false_records_nothing(self, tmp_path: Path):
        meta = self._import(
            tmp_path,
            {
                "isUpdatable": False,
                "indexUrl": "https://jitendex.org/index.json",
                "downloadUrl": "https://jitendex.org/jitendex.zip",
            },
        )
        assert "is_updatable" not in meta
        assert "index_url" not in meta

    def test_absent_update_fields_leave_meta_clean(self, tmp_path: Path):
        meta = self._import(tmp_path, {})
        for key in ("is_updatable", "index_url", "download_url", "author", "attribution", "description"):
            assert key not in meta
