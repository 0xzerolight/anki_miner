"""Tests for the Yomitan pitch-accent zip importer."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.pitch_accent import (
    YomitanPitchImportResult,
    import_yomitan_pitch_zip,
)
from anki_miner.services.pitch_accent_service import PitchAccentService
from tests.fixtures.pitch.build_yomitan_pitch_fixture import build_yomitan_pitch_zip


class TestHappyPath:
    def test_default_fixture_round_trips_via_pitch_accent_service(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "src.zip")
        dest = tmp_path / "pitch_accent.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        # default fixture: 3 usable + 1 display-only
        assert result.entry_count == 3
        assert result.skipped_display_only == 1

        text = dest.read_text(encoding="utf-8")
        lines = text.strip().splitlines()
        assert lines[0] == "reading,kanji,pattern"
        # rows sorted by reading then kanji
        assert lines[1:] == sorted(lines[1:])

        # PitchAccentService accepts the output unchanged.
        service = PitchAccentService(dest)
        service.load()
        assert service.lookup("猫") == "1"
        assert service.lookup("箸") == "1,2"
        assert service.lookup("ありがとう") == "2"

    def test_csv_header_exact(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]]],
        )
        dest = tmp_path / "out.csv"
        import_yomitan_pitch_zip(zip_path, dest)
        assert dest.read_text(encoding="utf-8").splitlines()[0] == "reading,kanji,pattern"


class TestMultiPosition:
    def test_multiple_drop_positions_joined_comma(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[[["箸", "pitch", {"reading": "はし", "pitches": [{"position": 0}, {"position": 2}]}]]],
        )
        dest = tmp_path / "out.csv"
        import_yomitan_pitch_zip(zip_path, dest)
        text = dest.read_text(encoding="utf-8")
        # row format: reading,kanji,pattern -> "はし,箸,0,2" (csv writer quotes when needed)
        assert "0,2" in text
        service = PitchAccentService(dest)
        service.load()
        assert service.lookup("箸") == "0,2"


class TestKanaOnly:
    def test_kana_only_term_leaves_kanji_column_empty(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[[["ありがとう", "pitch", {"reading": "ありがとう", "pitches": [{"position": 2}]}]]],
        )
        dest = tmp_path / "out.csv"
        import_yomitan_pitch_zip(zip_path, dest)
        # The kanji column must be empty for a kana-only term.
        import csv as _csv

        with open(dest, encoding="utf-8") as f:
            rows = list(_csv.reader(f))
        assert rows[0] == ["reading", "kanji", "pattern"]
        assert rows[1] == ["ありがとう", "", "2"]


class TestDisplayOnlySkip:
    def test_empty_pitches_counts_as_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["犬", "pitch", {"reading": "いぬ", "pitches": []}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1
        assert result.skipped_display_only == 1

    def test_missing_reading_counts_as_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["犬", "pitch", {"reading": "", "pitches": [{"position": 1}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1
        assert result.skipped_display_only == 1


class TestModeFilter:
    def test_freq_and_ipa_entries_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["猫", "freq", 100],
                    ["猫", "ipa", {"reading": "ねこ", "transcriptions": [{"ipa": "neko"}]}],
                    ["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 2


class TestErrors:
    def test_missing_zip(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="not found"):
            import_yomitan_pitch_zip(tmp_path / "nope.zip", tmp_path / "out.csv")

    def test_corrupt_zip(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"this is not a zip")
        with pytest.raises(SetupError, match="Corrupt zip"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_missing_index_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("term_meta_bank_1.json", "[]")
        with pytest.raises(SetupError, match="index.json"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_invalid_index_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", "not valid json")
            zf.writestr("term_meta_bank_1.json", "[]")
        with pytest.raises(SetupError, match="Invalid index.json"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_missing_title(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps({"revision": "v1", "format": 3}))
            zf.writestr(
                "term_meta_bank_1.json",
                json.dumps([["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]]),
            )
        with pytest.raises(SetupError, match="'title'"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    @pytest.mark.parametrize("bad_version", [1, 2, 0, 4])
    def test_unsupported_format_version_rejected(self, tmp_path: Path, bad_version: int) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            format_version=bad_version,
            meta_banks=[[["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]]],
        )
        with pytest.raises(SetupError, match=f"format version {bad_version}"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_missing_format_rejected(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            format_version=None,
            meta_banks=[[["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]]],
        )
        with pytest.raises(SetupError, match="format version None"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_zip_with_no_meta_banks(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "Empty", "revision": "v1", "format": 3}),
            )
        with pytest.raises(SetupError, match="term_meta_bank"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_invalid_meta_bank_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "Bad", "revision": "v1", "format": 3}),
            )
            zf.writestr("term_meta_bank_1.json", "{bad json}")
        with pytest.raises(SetupError, match="Invalid term_meta_bank_1"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_zip_with_path_traversal_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../escape.json", "{}")
        with pytest.raises(SetupError, match="unsafe|escaping"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")

    def test_empty_after_filtering_raises(self, tmp_path: Path) -> None:
        # All entries are display-only (empty pitches), so nothing usable remains.
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": []}],
                    ["犬", "pitch", {"reading": "いぬ", "pitches": []}],
                ]
            ],
        )
        with pytest.raises(SetupError, match="no usable pitch entries"):
            import_yomitan_pitch_zip(zip_path, tmp_path / "out.csv")


class TestDedup:
    def test_duplicate_kanji_reading_pair_first_wins(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 9}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1
        service = PitchAccentService(dest)
        service.load()
        assert service.lookup("猫") == "1"

    def test_homograph_distinct_readings_both_kept(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["開く", "pitch", {"reading": "ひらく", "pitches": [{"position": 2}]}],
                    ["開く", "pitch", {"reading": "あく", "pitches": [{"position": 0}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 2


class TestImportResult:
    def test_result_metadata_populated(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            title="NHK Pitch",
            revision="2024-01-01",
            meta_banks=[
                [
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["犬", "pitch", {"reading": "いぬ", "pitches": []}],  # skipped
                    ["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}],
                ]
            ],
        )
        result = import_yomitan_pitch_zip(zip_path, tmp_path / "pitch.csv")
        assert isinstance(result, YomitanPitchImportResult)
        assert result.source_name == "NHK Pitch"
        assert result.source_revision == "2024-01-01"
        assert result.entry_count == 2
        assert result.skipped_display_only == 1


class TestProgressAndCancel:
    def test_progress_fires_per_meta_bank_plus_done(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]],
                [["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}]],
                [["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}]],
            ],
        )
        events: list[tuple[int, int, str]] = []
        import_yomitan_pitch_zip(
            zip_path,
            tmp_path / "out.csv",
            progress=lambda cur, total, msg: events.append((cur, total, msg)),
        )
        assert events
        final_cur, final_total, _ = events[-1]
        assert final_cur == final_total

    def test_cancel_between_files_raises_and_leaves_dest_untouched(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.csv"
        dest.write_text("reading,kanji,pattern\npre,existing,0\n", encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}]],
                [["犬", "pitch", {"reading": "いぬ", "pitches": [{"position": 2}]}]],
            ],
        )
        with pytest.raises(SetupError, match="cancelled"):
            import_yomitan_pitch_zip(
                zip_path,
                dest,
                cancel_check=lambda: True,
            )
        assert dest.read_text(encoding="utf-8") == original
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()


class TestAtomicWrite:
    def test_existing_csv_preserved_on_failure(self, tmp_path: Path) -> None:
        dest = tmp_path / "pitch.csv"
        dest.write_text("reading,kanji,pattern\nold,旧,0\n", encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        # Build a zip that yields no usable entries — importer raises before write.
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "pitch", {"reading": "ねこ", "pitches": []}]]],
        )
        with pytest.raises(SetupError):
            import_yomitan_pitch_zip(zip_path, dest)

        assert dest.read_text(encoding="utf-8") == original
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()


class TestMalformedEntries:
    def test_short_entries_silently_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫"],
                    ["犬", "pitch"],
                    ["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1

    def test_empty_term_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1

    def test_non_dict_data_counts_as_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "pitch", "not-a-dict"],
                    ["鳥", "pitch", {"reading": "とり", "pitches": [{"position": 0}]}],
                ]
            ],
        )
        dest = tmp_path / "out.csv"
        result = import_yomitan_pitch_zip(zip_path, dest)
        assert result.entry_count == 1
        assert result.skipped_display_only == 1
