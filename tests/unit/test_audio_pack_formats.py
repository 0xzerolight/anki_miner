"""Unit tests for audio pack format detection and parsers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.services.audio_packs.formats import (
    PARSERS,
    detect_pack_format,
    parse_ajt,
    parse_forvo,
    parse_jpod_legacy,
    parse_nhk16,
    scan_importable_packs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_audio(directory: Path, name: str) -> Path:
    """Create a zero-byte audio stub in *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.touch()
    return p


# ---------------------------------------------------------------------------
# detect_pack_format
# ---------------------------------------------------------------------------


class TestDetectPackFormat:
    def test_ajt(self, tmp_path):
        _write_json(tmp_path / "index.json", {"headwords": {}, "files": {}})
        (tmp_path / "media").mkdir()
        assert detect_pack_format(tmp_path) == "ajt"

    def test_nhk16(self, tmp_path):
        _write_json(tmp_path / "entries.json", [])
        (tmp_path / "audio").mkdir()
        assert detect_pack_format(tmp_path) == "nhk16"

    def test_forvo(self, tmp_path):
        speaker_dir = tmp_path / "alice"
        _make_audio(speaker_dir, "食べる.mp3")
        assert detect_pack_format(tmp_path) == "forvo"

    def test_jpod_legacy(self, tmp_path):
        _make_audio(tmp_path, "たべる - 食べる.mp3")
        assert detect_pack_format(tmp_path) == "jpod_legacy"

    def test_unrecognised(self, tmp_path):
        (tmp_path / "random.txt").write_text("hello")
        assert detect_pack_format(tmp_path) is None

    def test_empty_dir(self, tmp_path):
        assert detect_pack_format(tmp_path) is None

    def test_path_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        assert detect_pack_format(f) is None

    def test_ajt_takes_priority_over_forvo(self, tmp_path):
        """If index.json + media/ exist, report ajt even if speaker dirs present."""
        _write_json(tmp_path / "index.json", {"headwords": {}, "files": {}})
        (tmp_path / "media").mkdir()
        speaker = tmp_path / "bob"
        _make_audio(speaker, "word.mp3")
        assert detect_pack_format(tmp_path) == "ajt"


# ---------------------------------------------------------------------------
# parse_ajt
# ---------------------------------------------------------------------------


class TestParseAjt:
    def _make_pack(self, tmp_path: Path, index_data: dict) -> Path:
        _write_json(tmp_path / "index.json", index_data)
        (tmp_path / "media").mkdir(exist_ok=True)
        return tmp_path

    def test_basic(self, tmp_path):
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "word.mp3").touch()
        index = {
            "headwords": {"食べる": ["word.mp3"]},
            "files": {"word.mp3": {"kana_reading": "たべる", "pitch_number": "2", "pitch_pattern": ""}},
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "test"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "食べる"
        assert r.reading == "たべる"
        assert r.source == "test"
        assert r.speaker is None
        assert r.display == "2"
        assert r.file == "media/word.mp3"

    def test_headword_with_multiple_files(self, tmp_path):
        (tmp_path / "media").mkdir()
        for fname in ["a.mp3", "b.mp3"]:
            (tmp_path / "media" / fname).touch()
        index = {
            "headwords": {"走る": ["a.mp3", "b.mp3"]},
            "files": {
                "a.mp3": {"kana_reading": "はしる", "pitch_number": "1", "pitch_pattern": "LH"},
                "b.mp3": {"kana_reading": "はしる", "pitch_number": "0", "pitch_pattern": "LHH"},
            },
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert len(rows) == 2
        assert {r.file for r in rows} == {"media/a.mp3", "media/b.mp3"}

    def test_missing_media_file_skipped(self, tmp_path):
        (tmp_path / "media").mkdir()
        # only a.mp3 exists on disk
        (tmp_path / "media" / "a.mp3").touch()
        index = {
            "headwords": {"走る": ["a.mp3", "missing.mp3"]},
            "files": {
                "a.mp3": {"kana_reading": "はしる", "pitch_number": "1", "pitch_pattern": ""},
                "missing.mp3": {"kana_reading": "はしる", "pitch_number": "0", "pitch_pattern": ""},
            },
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert len(rows) == 1
        assert rows[0].file == "media/a.mp3"

    def test_missing_files_entry_reading_none(self, tmp_path):
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "x.mp3").touch()
        index = {
            "headwords": {"犬": ["x.mp3"]},
            "files": {},  # no entry for x.mp3
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert rows[0].reading is None

    def test_pitch_number_zero_int_preserved(self, tmp_path):
        """pitch_number integer 0 (heiban) must not be dropped by falsy guard."""
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "h.mp3").touch()
        index = {
            "headwords": {"走る": ["h.mp3"]},
            "files": {"h.mp3": {"kana_reading": "はしる", "pitch_number": 0, "pitch_pattern": "LHH"}},
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert rows[0].display == "0"

    def test_pitch_number_question_mark_uses_pattern(self, tmp_path):
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "y.mp3").touch()
        index = {
            "headwords": {"猫": ["y.mp3"]},
            "files": {"y.mp3": {"kana_reading": "ねこ", "pitch_number": "?", "pitch_pattern": "LH"}},
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert rows[0].display == "LH"

    def test_compound_pitch_number_uses_pattern(self, tmp_path):
        """pitch_number like '0+2' is not a plain digit → fall through to pitch_pattern."""
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "c.mp3").touch()
        index = {
            "headwords": {"花": ["c.mp3"]},
            "files": {"c.mp3": {"kana_reading": "はな", "pitch_number": "0+2", "pitch_pattern": "LHH"}},
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert rows[0].display == "LHH"

    def test_no_pitch_info_display_none(self, tmp_path):
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "z.mp3").touch()
        index = {
            "headwords": {"山": ["z.mp3"]},
            "files": {"z.mp3": {"kana_reading": "やま"}},
        }
        _write_json(tmp_path / "index.json", index)
        rows = list(parse_ajt(tmp_path, "src"))
        assert rows[0].display is None

    def test_malformed_json_raises(self, tmp_path):
        (tmp_path / "media").mkdir()
        (tmp_path / "index.json").write_text("not json", encoding="utf-8")
        with pytest.raises(ValueError, match="Malformed"):
            list(parse_ajt(tmp_path, "src"))

    def test_not_object_raises(self, tmp_path):
        (tmp_path / "media").mkdir()
        _write_json(tmp_path / "index.json", [1, 2, 3])
        with pytest.raises(ValueError):
            list(parse_ajt(tmp_path, "src"))


# ---------------------------------------------------------------------------
# parse_nhk16
# ---------------------------------------------------------------------------


class TestParseNhk16:
    def _make_pack(self, tmp_path: Path, entries: list) -> Path:
        (tmp_path / "audio").mkdir(exist_ok=True)
        _write_json(tmp_path / "entries.json", entries)
        return tmp_path

    def test_basic_kanji_entry(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "abc.mp3").touch()
        entries = [
            {
                "kana": "たべる",
                "kanji": ["食べる"],
                "accents": [{"soundFile": "abc.mp3"}],
                "subentries": [],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "食べる"
        assert r.reading == "たべる"
        assert r.source == "nhk"
        assert r.file == "audio/abc.mp3"
        assert r.display is None

    def test_kanji_list_with_fullwidth_comma_subsplit(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "split.mp3").touch()
        entries = [
            {
                "kana": "はし",
                "kanji": ["橋，箸"],
                "accents": [{"soundFile": "split.mp3"}],
                "subentries": [],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        expressions = [r.expression for r in rows]
        assert "橋" in expressions
        assert "箸" in expressions
        assert len(rows) == 2

    def test_null_sound_file_skipped(self, tmp_path):
        (tmp_path / "audio").mkdir()
        entries = [
            {
                "kana": "いぬ",
                "kanji": ["犬"],
                "accents": [{"soundFile": None}],
                "subentries": [],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert rows == []

    def test_missing_audio_file_skipped(self, tmp_path):
        (tmp_path / "audio").mkdir()
        entries = [
            {
                "kana": "ねこ",
                "kanji": ["猫"],
                "accents": [{"soundFile": "ghost.mp3"}],
                "subentries": [],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert rows == []

    def test_kana_only_entry(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "kana.mp3").touch()
        entries = [
            {
                "kana": "はい",
                "kanji": [],
                "accents": [{"soundFile": "kana.mp3"}],
                "subentries": [],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert len(rows) == 1
        assert rows[0].expression == "はい"
        assert rows[0].reading == "はい"

    def test_subentry_with_kana_head(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "sub_kana.mp3").touch()
        entries = [
            {
                "kana": "みず",
                "kanji": ["水"],
                "accents": [],
                "subentries": [{"head": "みずいろ", "accents": [{"soundFile": "sub_kana.mp3"}]}],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "みずいろ"
        # kana head → reading = head
        assert r.reading == "みずいろ"

    def test_subentry_with_kanji_head(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "sub_kanji.mp3").touch()
        entries = [
            {
                "kana": "みず",
                "kanji": ["水"],
                "accents": [],
                "subentries": [{"head": "水色", "accents": [{"soundFile": "sub_kanji.mp3"}]}],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "水色"
        # kanji head → reading = None
        assert r.reading is None

    def test_subentry_without_head_skipped(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "counter.mp3").touch()
        entries = [
            {
                "kana": "いち",
                "kanji": ["一"],
                "accents": [],
                "subentries": [
                    # no "head" key → counter entry, should be skipped
                    {"accents": [{"soundFile": "counter.mp3"}]}
                ],
            }
        ]
        _write_json(tmp_path / "entries.json", entries)
        rows = list(parse_nhk16(tmp_path, "nhk"))
        assert rows == []

    def test_malformed_json_raises(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "entries.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(ValueError, match="Malformed"):
            list(parse_nhk16(tmp_path, "nhk"))

    def test_not_array_raises(self, tmp_path):
        (tmp_path / "audio").mkdir()
        _write_json(tmp_path / "entries.json", {"key": "value"})
        with pytest.raises(ValueError):
            list(parse_nhk16(tmp_path, "nhk"))


# ---------------------------------------------------------------------------
# parse_forvo
# ---------------------------------------------------------------------------


class TestParseForvo:
    def test_speaker_from_parent_dir(self, tmp_path):
        speaker_dir = tmp_path / "bob"
        _make_audio(speaker_dir, "食べる.mp3")
        rows = list(parse_forvo(tmp_path, "forvo"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "食べる"
        assert r.reading is None
        assert r.speaker == "bob"
        assert r.display == "bob"
        assert r.source == "forvo"

    def test_nested_depth(self, tmp_path):
        """Files in nested subdirs are included; speaker is their immediate parent."""
        deep = tmp_path / "alice" / "subdir"
        _make_audio(deep, "word.ogg")
        rows = list(parse_forvo(tmp_path, "forvo"))
        assert len(rows) == 1
        assert rows[0].speaker == "subdir"

    def test_non_audio_ext_ignored(self, tmp_path):
        speaker_dir = tmp_path / "charlie"
        speaker_dir.mkdir()
        (speaker_dir / "notes.txt").touch()
        (speaker_dir / "image.png").touch()
        _make_audio(speaker_dir, "word.flac")
        rows = list(parse_forvo(tmp_path, "forvo"))
        assert len(rows) == 1
        assert rows[0].expression == "word"

    def test_multiple_speakers(self, tmp_path):
        for speaker in ["alice", "bob"]:
            _make_audio(tmp_path / speaker, "日本語.mp3")
        rows = list(parse_forvo(tmp_path, "forvo"))
        assert len(rows) == 2
        speakers = {r.speaker for r in rows}
        assert speakers == {"alice", "bob"}

    def test_relative_posix_path(self, tmp_path):
        _make_audio(tmp_path / "alice", "test.mp3")
        rows = list(parse_forvo(tmp_path, "forvo"))
        assert "/" in rows[0].file
        assert "\\" not in rows[0].file
        assert not rows[0].file.startswith("/")


# ---------------------------------------------------------------------------
# parse_jpod_legacy
# ---------------------------------------------------------------------------


class TestParseJpodLegacy:
    def test_normal_stem(self, tmp_path):
        _make_audio(tmp_path, "たべる - 食べる.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "食べる"
        assert r.reading == "たべる"
        assert r.speaker is None
        assert r.display is None

    def test_reading_equals_expression_kana(self, tmp_path):
        """reading == expression AND all-kana → expression=reading, reading=reading."""
        _make_audio(tmp_path, "はい - はい.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "はい"
        assert r.reading == "はい"

    def test_reading_equals_expression_not_kana(self, tmp_path):
        """reading == expression AND NOT kana → expression=reading, reading=None."""
        _make_audio(tmp_path, "食べる - 食べる.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert len(rows) == 1
        r = rows[0]
        assert r.expression == "食べる"
        assert r.reading is None

    def test_malformed_stem_skipped(self, tmp_path):
        """Stems with no ' - ' separator are silently skipped."""
        _make_audio(tmp_path, "nodash.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert rows == []

    def test_wrong_separator_count_skipped(self, tmp_path):
        """Stems with more than one ' - ' yield 3 parts after split and are skipped."""
        # "a - b - c".split(" - ") → ["a", "b", "c"] which is 3 parts → skip
        _make_audio(tmp_path, "a - b - c.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert rows == []

    def test_nested_files(self, tmp_path):
        subdir = tmp_path / "sub"
        _make_audio(subdir, "はしる - 走る.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert len(rows) == 1
        assert rows[0].expression == "走る"

    def test_relative_posix_path(self, tmp_path):
        subdir = tmp_path / "sub"
        _make_audio(subdir, "ねこ - 猫.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert "/" in rows[0].file
        assert "\\" not in rows[0].file

    def test_katakana_reading_equals_expression(self, tmp_path):
        """Katakana-only reading==expression → treated as kana → reading preserved."""
        _make_audio(tmp_path, "コーヒー - コーヒー.mp3")
        rows = list(parse_jpod_legacy(tmp_path, "jpod"))
        assert len(rows) == 1
        assert rows[0].reading == "コーヒー"
        assert rows[0].expression == "コーヒー"


# ---------------------------------------------------------------------------
# PARSERS dispatch table
# ---------------------------------------------------------------------------


class TestParsersDict:
    def test_all_formats_present(self):
        assert set(PARSERS.keys()) == {"ajt", "nhk16", "forvo", "jpod_legacy"}

    def test_parsers_are_callable(self):
        for fmt, fn in PARSERS.items():
            assert callable(fn), f"{fmt} parser is not callable"


# ---------------------------------------------------------------------------
# scan_importable_packs
# ---------------------------------------------------------------------------


class TestScanImportablePacks:
    def test_dir_itself_is_pack(self, tmp_path):
        _write_json(tmp_path / "index.json", {"headwords": {}, "files": {}})
        (tmp_path / "media").mkdir()
        results = scan_importable_packs(tmp_path)
        assert (tmp_path, "ajt") in results

    def test_multiple_child_packs(self, tmp_path):
        # ajt child
        ajt = tmp_path / "ajt_pack"
        ajt.mkdir()
        _write_json(ajt / "index.json", {"headwords": {}, "files": {}})
        (ajt / "media").mkdir()

        # nhk16 child
        nhk = tmp_path / "nhk_pack"
        nhk.mkdir()
        _write_json(nhk / "entries.json", [])
        (nhk / "audio").mkdir()

        results = scan_importable_packs(tmp_path)
        assert (ajt, "ajt") in results
        assert (nhk, "nhk16") in results

    def test_hidden_dirs_skipped(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        _write_json(hidden / "index.json", {"headwords": {}, "files": {}})
        (hidden / "media").mkdir()
        results = scan_importable_packs(tmp_path)
        assert not any(p == hidden for p, _ in results)

    def test_unrecognised_children_excluded(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "readme.txt").write_text("nothing")
        results = scan_importable_packs(tmp_path)
        assert results == []

    def test_parent_with_children_and_itself(self, tmp_path):
        """If parent is also a pack, it should be included."""
        _write_json(tmp_path / "index.json", {"headwords": {}, "files": {}})
        (tmp_path / "media").mkdir()

        # forvo child: must have speaker subdirs with audio files
        forvo_child = tmp_path / "forvo_pack"
        _make_audio(forvo_child / "alice", "word.mp3")

        results = scan_importable_packs(tmp_path)
        paths = {p for p, _ in results}
        assert tmp_path in paths
        # forvo_pack is a child with speaker subdir → detected as forvo
        assert forvo_child in paths

    def test_empty_directory(self, tmp_path):
        assert scan_importable_packs(tmp_path) == []

    def test_canonical_user_files_parent_yields_only_children(self, tmp_path):
        """A canonical user_files/ parent must yield ONLY its child packs.

        The heuristic formats (forvo/jpod_legacy) match on audio files below
        the directory, so without the children-first rule the parent itself
        would be misreported as a junk "forvo"/"jpod_legacy" pack built from
        its children's audio files.
        """
        user_files = tmp_path / "user_files"

        # jpod_files: flat "{reading} - {expression}" stems
        jpod = user_files / "jpod_files"
        _make_audio(jpod, "たべる - 食べる.mp3")
        _make_audio(jpod, "のむ - 飲む.mp3")

        # nhk16_files: entries.json + audio/
        nhk = user_files / "nhk16_files"
        nhk.mkdir(parents=True)
        _write_json(nhk / "entries.json", [])
        (nhk / "audio").mkdir()

        # forvo_files: speaker dirs with audio files
        forvo = user_files / "forvo_files"
        _make_audio(forvo / "alice", "走る.mp3")

        results = scan_importable_packs(user_files)

        assert sorted(results) == sorted(
            [
                (jpod, "jpod_legacy"),
                (nhk, "nhk16"),
                (forvo, "forvo"),
            ]
        )
        assert not any(p == user_files for p, _ in results), "parent must never be reported as a pack"
