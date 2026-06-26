"""Tests for the Yomitan frequency zip importer."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency.csv_parse import _extract_word_rank, _is_word_first_header
from anki_miner.services.frequency.yomitan_freq_importer import (
    YomitanFreqImportResult,
    import_yomitan_freq_zip,
)
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row
from tests.fixtures.frequency.build_yomitan_freq_fixture import build_yomitan_freq_zip


def _load_freq_csv(path: Path) -> dict[str, int]:
    """Read a frequency CSV the way a downstream loader does (first-wins).

    Mirrors the legacy single-CSV load loop (delimiter detect + header skip +
    first-occurrence-wins) so these importer round-trip assertions survive the
    removal of FrequencyService. The row-shape logic is the same shared
    ``csv_parse`` helpers the importer itself uses.
    """
    data: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        sample = f.read(4096)
        f.seek(0)
        reader = csv.reader(f, delimiter=detect_delimiter(sample))
        first_row = True
        word_first = False
        for row in reader:
            if len(row) < 2:
                continue
            if first_row:
                first_row = False
                if is_header_row(row):
                    word_first = _is_word_first_header(row)
                    continue
            word, rank = _extract_word_rank(row, word_first=word_first)
            if word and rank is not None and word not in data:
                data[word] = rank
    return data


class TestNormalization:
    """Cover all five spec-defined `freq` data shapes."""

    @pytest.mark.parametrize(
        "data,expected_rank",
        [
            # 1. bare number
            (1234, 1234),
            # 2a. string with integer
            ("1234", 1234),
            # 2b. display-only string (drops)
            ("①", None),
            ("高", None),
            ("", None),
            # 3. GenericFrequencyData
            ({"value": 1234, "displayValue": "1234"}, 1234),
            ({"value": 42}, 42),
            ({"value": "42"}, 42),
            # 4. TermMetaFrequencyDataWithReading + bare number frequency
            ({"reading": "ねこ", "frequency": 1234}, 1234),
            ({"reading": "ねこ", "frequency": "1234"}, 1234),
            # 5. TermMetaFrequencyDataWithReading + GenericFrequencyData
            (
                {"reading": "ねこ", "frequency": {"value": 1234, "displayValue": "1234"}},
                1234,
            ),
        ],
    )
    def test_shapes(self, tmp_path: Path, data: object, expected_rank: int | None) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "freq", data]]],
        )
        dest = tmp_path / "frequency.csv"
        if expected_rank is None:
            # Empty / display-only entries — importer raises because the dict
            # yields no usable rows.
            with pytest.raises(SetupError, match="no usable frequency entries"):
                import_yomitan_freq_zip(zip_path, dest)
        else:
            result = import_yomitan_freq_zip(zip_path, dest)
            assert result.entry_count == 1
            assert _load_freq_csv(dest).get("猫") == expected_rank


class TestInvalidRank:
    """Yomitan rank must be a positive integer (>= 1); reject 0 and negatives."""

    @pytest.mark.parametrize(
        "data",
        [
            -5,
            0,
            "-1",
            "0",
            {"value": -1},
            {"value": 0},
            {"reading": "ねこ", "frequency": -7},
            {"reading": "ねこ", "frequency": {"value": 0}},
        ],
    )
    def test_non_positive_rank_treated_as_unusable(self, tmp_path: Path, data: object) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "freq", data]]],
        )
        dest = tmp_path / "frequency.csv"
        with pytest.raises(SetupError, match="no usable frequency entries"):
            import_yomitan_freq_zip(zip_path, dest)

    def test_invalid_rank_counted_in_skipped_display_only(self, tmp_path: Path) -> None:
        # One valid + one negative entry — importer succeeds, skip count == 1.
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "freq", 100],
                    ["犬", "freq", -1],
                ]
            ],
        )
        dest = tmp_path / "frequency.csv"
        result = import_yomitan_freq_zip(zip_path, dest)
        assert result.entry_count == 1
        assert result.skipped_display_only == 1


class TestModeFilter:
    def test_pitch_and_ipa_entries_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫", "freq", 100],
                    ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
                    ["猫", "ipa", {"reading": "ねこ", "transcriptions": [{"ipa": "neko"}]}],
                    ["犬", "freq", 200],
                ]
            ],
        )
        dest = tmp_path / "frequency.csv"
        result = import_yomitan_freq_zip(zip_path, dest)
        assert result.entry_count == 2


class TestMultipleMetaBanks:
    def test_concatenated_across_files(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [["猫", "freq", 100]],
                [["犬", "freq", 200]],
                [["鳥", "freq", 300]],
            ],
        )
        dest = tmp_path / "frequency.csv"
        result = import_yomitan_freq_zip(zip_path, dest)
        assert result.entry_count == 3

        loaded = _load_freq_csv(dest)
        assert loaded.get("猫") == 100
        assert loaded.get("犬") == 200
        assert loaded.get("鳥") == 300


class TestReadingCollision:
    def test_min_rank_wins_for_term(self, tmp_path: Path) -> None:
        # 開く is the BCCWJ poster child for term/reading collision:
        # ひらく ≠ あく, with different frequencies in real corpora.
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["開く", "freq", {"reading": "ひらく", "frequency": 250}],
                    ["開く", "freq", {"reading": "あく", "frequency": 800}],
                ]
            ],
        )
        dest = tmp_path / "frequency.csv"
        result = import_yomitan_freq_zip(zip_path, dest)
        assert result.entry_count == 1

        assert _load_freq_csv(dest).get("開く") == 250  # min rank wins


class TestFormatVersion:
    """Importer requires Yomitan format v3; reject v1/v2/missing explicitly."""

    @pytest.mark.parametrize("bad_version", [1, 2, 0, 4])
    def test_unsupported_format_version_rejected(self, tmp_path: Path, bad_version: int) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            format_version=bad_version,
            meta_banks=[[["猫", "freq", 100]]],
        )
        with pytest.raises(SetupError, match=f"format version {bad_version}"):
            import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")

    def test_missing_format_rejected(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            format_version=None,
            meta_banks=[[["猫", "freq", 100]]],
        )
        with pytest.raises(SetupError, match="format version None"):
            import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")

    def test_format_v3_accepted(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            format_version=3,
            meta_banks=[[["猫", "freq", 100]]],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert result.entry_count == 1


class TestOccurrenceBasedRejected:
    def test_raises_with_helpful_message(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            frequency_mode="occurrence-based",
            meta_banks=[[["猫", "freq", 50_000]]],
        )
        with pytest.raises(SetupError, match="occurrence-based"):
            import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")

    def test_rank_based_mode_accepted(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            frequency_mode="rank-based",
            meta_banks=[[["猫", "freq", 100]]],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert result.entry_count == 1

    def test_missing_frequency_mode_accepted(self, tmp_path: Path) -> None:
        # frequencyMode is optional per spec; treat absence as rank-based.
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "freq", 100]]],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert result.entry_count == 1


class TestErrors:
    def test_zip_with_no_meta_banks(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "Empty", "revision": "v1", "format": 3}),
            )
        with pytest.raises(SetupError, match="term_meta_bank"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_missing_zip(self, tmp_path: Path) -> None:
        with pytest.raises(SetupError, match="not found"):
            import_yomitan_freq_zip(tmp_path / "nope.zip", tmp_path / "out.csv")

    def test_corrupt_zip(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"this is not a zip")
        with pytest.raises(SetupError, match="Corrupt zip"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_missing_index_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("term_meta_bank_1.json", "[]")
        with pytest.raises(SetupError, match="index.json"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_invalid_index_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", "not valid json")
            zf.writestr("term_meta_bank_1.json", "[]")
        with pytest.raises(SetupError, match="Invalid index.json"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_invalid_meta_bank_json(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "index.json",
                json.dumps({"title": "Bad", "revision": "v1", "format": 3}),
            )
            zf.writestr("term_meta_bank_1.json", "{bad json}")
        with pytest.raises(SetupError, match="Invalid term_meta_bank_1"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_missing_title(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("index.json", json.dumps({"revision": "v1", "format": 3}))
            zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 1]]))
        with pytest.raises(SetupError, match="'title'"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")

    def test_zip_with_path_traversal_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../escape.json", "{}")
        with pytest.raises(SetupError, match="unsafe|escaping"):
            import_yomitan_freq_zip(zip_path, tmp_path / "out.csv")


class TestOutputFormat:
    def test_csv_has_header_and_is_parseable_by_frequency_service(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "freq", 100], ["犬", "freq", 200]]],
        )
        dest = tmp_path / "frequency.csv"
        import_yomitan_freq_zip(zip_path, dest)

        text = dest.read_text(encoding="utf-8")
        lines = text.strip().splitlines()
        assert lines[0] == "term,rank"
        assert "猫,100" in lines
        assert "犬,200" in lines

        # End-to-end: the written CSV round-trips through the shared loader.
        loaded = _load_freq_csv(dest)
        assert len(loaded) == 2
        assert loaded.get("猫") == 100

    def test_atomic_write_preserves_existing_on_failure(self, tmp_path: Path) -> None:
        # Pre-seed an existing CSV the user already had.
        dest = tmp_path / "frequency.csv"
        dest.write_text("term,rank\nfoo,1\n", encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        # Build a zip that will fail (occurrence-based mode).
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            frequency_mode="occurrence-based",
            meta_banks=[[["猫", "freq", 100]]],
        )
        with pytest.raises(SetupError):
            import_yomitan_freq_zip(zip_path, dest)

        # Existing CSV must be untouched — and no `.tmp` stub left behind
        # (the failure happens before we ever stage the temp file).
        assert dest.read_text(encoding="utf-8") == original
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()

    def test_no_tmp_left_when_writing_rows_fails(self, tmp_path: Path, monkeypatch) -> None:
        """A failure DURING row writing must not orphan the .tmp file (T-40)."""
        import anki_miner.services.yomitan_meta_bank as mod

        dest = tmp_path / "frequency.csv"
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[[["猫", "freq", 100]]],
        )

        class ExplodingWriter:
            def __init__(self, *_a, **_k):
                pass

            def writerow(self, *_a, **_k):
                raise OSError("disk full mid-write")

        monkeypatch.setattr(mod.csv, "writer", lambda *a, **k: ExplodingWriter())

        with pytest.raises(OSError):
            import_yomitan_freq_zip(zip_path, dest)

        assert not dest.exists()
        assert not dest.with_suffix(dest.suffix + ".tmp").exists()


class TestImportResult:
    def test_result_metadata_populated(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            title="JPDB",
            revision="2024-01-01",
            meta_banks=[
                [
                    ["猫", "freq", 100],
                    ["犬", "freq", "①"],  # display-only — skipped
                    ["鳥", "freq", 300],
                ]
            ],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert isinstance(result, YomitanFreqImportResult)
        assert result.source_name == "JPDB"
        assert result.source_revision == "2024-01-01"
        assert result.entry_count == 2
        assert result.skipped_display_only == 1


class TestProgressCallback:
    def test_fires_per_meta_bank_plus_done(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [["猫", "freq", 100]],
                [["犬", "freq", 200]],
                [["鳥", "freq", 300]],
            ],
        )
        events: list[tuple[int, int, str]] = []
        import_yomitan_freq_zip(
            zip_path,
            tmp_path / "frequency.csv",
            progress=lambda cur, total, msg: events.append((cur, total, msg)),
        )
        assert events  # at least one event
        final_cur, final_total, _ = events[-1]
        assert final_cur == final_total

    def test_cancel_aborts_between_files(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [["猫", "freq", 100]],
                [["犬", "freq", 200]],
            ],
        )
        with pytest.raises(SetupError, match="cancelled"):
            import_yomitan_freq_zip(
                zip_path,
                tmp_path / "frequency.csv",
                cancel_check=lambda: True,
            )


class TestMalformedEntries:
    def test_short_entries_silently_skipped(self, tmp_path: Path) -> None:
        # Entries with fewer than 3 elements aren't valid `[term, mode, data]`
        # triples; the importer skips them rather than raising — same charity
        # as the definition importer.
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["猫"],  # too short
                    ["犬", "freq"],  # too short
                    ["鳥", "freq", 300],  # valid
                ]
            ],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert result.entry_count == 1

    def test_empty_term_skipped(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_freq_zip(
            tmp_path / "src.zip",
            meta_banks=[
                [
                    ["", "freq", 100],
                    ["鳥", "freq", 300],
                ]
            ],
        )
        result = import_yomitan_freq_zip(zip_path, tmp_path / "frequency.csv")
        assert result.entry_count == 1
