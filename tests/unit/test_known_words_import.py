"""Tests for the known-words file importer (services/known_words_import.py).

Fixture shapes are transcribed from the verified upstream sources cited in the
module docstring (jpdb review export consumers, AnkiMorphs exporter source,
Migaku Word Exporter README) — do not "fix" them to match the parser; the
parser must match them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.services.known_words_import import (
    FORMAT_KEYS,
    KnownWordsImportError,
    KnownWordsImportResult,
    parse_known_words_file,
)


def _write(tmp_path: Path, name: str, text: str, *, bom: bool = False) -> Path:
    path = tmp_path / name
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    return path


def _jpdb_card(spelling: str, reviews: list[dict]) -> dict:
    return {"vid": 1, "spelling": spelling, "reading": "よみ", "reviews": reviews}


def _jpdb_file(tmp_path: Path, cards: list[dict], *, bom: bool = False) -> Path:
    return _write(tmp_path, "reviews.json", json.dumps({"cards_vocabulary_jp_en": cards}), bom=bom)


class TestJpdb:
    def test_positive_last_grades_qualify(self, tmp_path):
        cards = [
            _jpdb_card(w, [{"timestamp": 100, "grade": g}])
            for w, g in [("一", "okay"), ("二", "easy"), ("三", "hard"), ("四", "pass"), ("五", "known")]
        ]
        result = parse_known_words_file(_jpdb_file(tmp_path, cards))
        assert result.format_key == "jpdb"
        assert result.words == frozenset({"一", "二", "三", "四", "五"})
        assert result.total_entries == 5

    def test_negative_last_grades_excluded(self, tmp_path):
        cards = [
            _jpdb_card(w, [{"timestamp": 100, "grade": g}])
            for w, g in [("一", "nothing"), ("二", "something"), ("三", "fail"), ("四", "unknown")]
        ] + [_jpdb_card("言葉", [{"timestamp": 100, "grade": "okay"}])]
        result = parse_known_words_file(_jpdb_file(tmp_path, cards))
        assert result.words == frozenset({"言葉"})
        assert result.total_entries == 5

    def test_zero_reviews_card_excluded(self, tmp_path):
        cards = [_jpdb_card("言葉", []), _jpdb_card("犬", [{"timestamp": 1, "grade": "okay"}])]
        result = parse_known_words_file(_jpdb_file(tmp_path, cards))
        assert result.words == frozenset({"犬"})

    def test_unrecognized_grade_counts_as_known(self, tmp_path):
        # Exclude-list rule: future positive states (e.g. never-forget) must not drop.
        cards = [_jpdb_card("言葉", [{"timestamp": 1, "grade": "never-forget"}])]
        result = parse_known_words_file(_jpdb_file(tmp_path, cards))
        assert result.words == frozenset({"言葉"})

    def test_latest_review_wins_regardless_of_order(self, tmp_path):
        reviews = [
            {"timestamp": 300, "grade": "okay"},
            {"timestamp": 100, "grade": "fail"},
            {"timestamp": 200, "grade": "fail"},
        ]
        result = parse_known_words_file(_jpdb_file(tmp_path, [_jpdb_card("言葉", reviews)]))
        assert result.words == frozenset({"言葉"})

        reviews_bad_tail = [
            {"timestamp": 100, "grade": "okay"},
            {"timestamp": 300, "grade": "fail"},
            {"timestamp": 200, "grade": "okay"},
        ]
        with pytest.raises(KnownWordsImportError):
            parse_known_words_file(_jpdb_file(tmp_path, [_jpdb_card("言葉", reviews_bad_tail)]))

    def test_non_numeric_timestamp_does_not_crash(self, tmp_path):
        # A drifted/hand-edited export can carry a non-numeric timestamp; it must
        # coerce to 0 for the latest-review sort instead of raising TypeError on
        # the mixed str-vs-int comparison (which would escape the
        # KnownWordsImportError contract as a generic "unexpected error"). The
        # numeric review still wins the recency comparison.
        reviews = [
            {"timestamp": 100, "grade": "okay"},
            {"timestamp": "2026-01-01", "grade": "fail"},
        ]
        result = parse_known_words_file(_jpdb_file(tmp_path, [_jpdb_card("言葉", reviews)]))
        assert result.words == frozenset({"言葉"})

    def test_bom_prefixed_json_still_detected(self, tmp_path):
        cards = [_jpdb_card("言葉", [{"timestamp": 1, "grade": "okay"}])]
        result = parse_known_words_file(_jpdb_file(tmp_path, cards, bom=True))
        assert result.format_key == "jpdb"
        assert result.words == frozenset({"言葉"})


class TestMigakuJson:
    def test_known_status_only(self, tmp_path):
        payload = {
            "exported": "2026-03-15T12:00:00.000Z",
            "words": [
                {"word": "食べる", "reading": "たべる", "language": "ja", "status": "KNOWN"},
                {"word": "犬", "reading": "いぬ", "language": "ja", "status": "LEARNING"},
                {"word": "猫", "reading": "ねこ", "language": "ja", "status": "IGNORED"},
            ],
        }
        result = parse_known_words_file(_write(tmp_path, "migaku.json", json.dumps(payload)))
        assert result.format_key == "migaku_json"
        assert result.words == frozenset({"食べる"})
        assert result.total_entries == 3


class TestMigakuLegacy:
    def test_status_two_is_known(self, tmp_path):
        payload = [["言葉", 2], ["犬", 1], ["猫", 2]]
        result = parse_known_words_file(_write(tmp_path, "backup.json", json.dumps(payload)))
        assert result.format_key == "migaku_legacy"
        assert result.words == frozenset({"言葉", "猫"})
        assert result.total_entries == 3


class TestMigakuCsv:
    def test_known_rows_only(self, tmp_path):
        text = "Word,Reading,Language,Status\n食べる,たべる,ja,KNOWN\n犬,いぬ,ja,LEARNING\n"
        result = parse_known_words_file(_write(tmp_path, "words.csv", text))
        assert result.format_key == "migaku_csv"
        assert result.words == frozenset({"食べる"})
        assert result.total_entries == 2

    def test_header_match_is_case_insensitive(self, tmp_path):
        text = "word,reading,language,status\n食べる,たべる,ja,known\n"
        result = parse_known_words_file(_write(tmp_path, "words.csv", text))
        assert result.format_key == "migaku_csv"
        assert result.words == frozenset({"食べる"})

    def test_bom_prefixed_header_still_detected(self, tmp_path):
        text = "Word,Reading,Language,Status\n食べる,たべる,ja,KNOWN\n"
        result = parse_known_words_file(_write(tmp_path, "words.csv", text, bom=True))
        assert result.format_key == "migaku_csv"
        assert result.words == frozenset({"食べる"})


class TestAnkiMorphs:
    def test_lemma_only_export(self, tmp_path):
        text = "Morph-Lemma\n食べる\n走る\n"
        result = parse_known_words_file(_write(tmp_path, "known_morphs.csv", text))
        assert result.format_key == "ankimorphs"
        assert result.words == frozenset({"食べる", "走る"})
        assert result.total_entries == 2

    def test_inflections_ignored(self, tmp_path):
        text = "Morph-Lemma,Morph-Inflection,Occurrence\n" "食べる,食べた,3\n" "食べる,食べて,2\n" "走る,走った,1\n"
        result = parse_known_words_file(_write(tmp_path, "known_morphs.csv", text))
        assert result.format_key == "ankimorphs"
        assert result.words == frozenset({"食べる", "走る"})
        assert result.total_entries == 3


class TestGenericFallback:
    def test_txt_one_word_per_line(self, tmp_path):
        text = "食べる\n\n# comment line\n犬\n"
        result = parse_known_words_file(_write(tmp_path, "words.txt", text))
        assert result.format_key == "generic"
        assert result.words == frozenset({"食べる", "犬"})
        assert result.total_entries == 2

    def test_csv_first_cell(self, tmp_path):
        # jpdb-userscript deck export shape: headerless "spelling,reading," lines.
        text = "食べる,たべる,\n犬,いぬ,\n"
        result = parse_known_words_file(_write(tmp_path, "deck.csv", text))
        assert result.format_key == "generic"
        assert result.words == frozenset({"食べる", "犬"})

    def test_tab_delimited_first_cell(self, tmp_path):
        text = "食べる\tたべる\n犬\tいぬ\n"
        result = parse_known_words_file(_write(tmp_path, "words.txt", text))
        assert result.words == frozenset({"食べる", "犬"})

    def test_whitespace_stripped_and_duplicates_collapsed(self, tmp_path):
        text = "  食べる  \n食べる\n"
        result = parse_known_words_file(_write(tmp_path, "words.txt", text))
        assert result.words == frozenset({"食べる"})


class TestErrors:
    def test_missing_file_is_unreadable(self, tmp_path):
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(tmp_path / "nope.txt")
        assert exc.value.reason == "unreadable"

    def test_unmatched_json_never_falls_through_to_line_reader(self, tmp_path):
        path = _write(tmp_path, "other.json", json.dumps({"cards": ["言葉", "犬"]}))
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(path)
        assert exc.value.reason == "unrecognized"

    def test_json_suffix_with_invalid_json_is_unrecognized(self, tmp_path):
        # A .json file must never be read as a generic word list.
        path = _write(tmp_path, "broken.json", '{"words": [truncated')
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(path)
        assert exc.value.reason == "unrecognized"

    def test_empty_file_has_no_words(self, tmp_path):
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(_write(tmp_path, "empty.txt", ""))
        assert exc.value.reason == "no_known_words"

    def test_structured_match_with_zero_qualifying_reports_no_known_words(self, tmp_path):
        text = "Word,Reading,Language,Status\n犬,いぬ,ja,LEARNING\n"
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(_write(tmp_path, "words.csv", text))
        assert exc.value.reason == "no_known_words"
        assert exc.value.format_key == "migaku_csv"


class TestEncodingAndSize:
    def test_shift_jis_generic_list_decodes(self, tmp_path):
        # Japanese Notepad/Excel default; utf-8 fails, cp932 fallback recovers it.
        path = tmp_path / "sjis.txt"
        path.write_bytes("食べる\n犬\n".encode("cp932"))
        result = parse_known_words_file(path)
        assert result.words == frozenset({"食べる", "犬"})

    def test_oversized_file_is_unreadable(self, tmp_path, monkeypatch):
        import anki_miner.services.known_words_import as kwi

        monkeypatch.setattr(kwi, "_MAX_IMPORT_BYTES", 8)
        with pytest.raises(KnownWordsImportError) as exc:
            parse_known_words_file(_write(tmp_path, "big.txt", "食べる\n犬\n猫\n"))
        assert exc.value.reason == "unreadable"

    def test_json_word_is_stripped(self, tmp_path):
        # A padded JSON word must be stored stripped so it can match a card front.
        data = {"words": [{"word": "  食べる  ", "status": "KNOWN"}]}
        result = parse_known_words_file(_write(tmp_path, "migaku.json", json.dumps(data)))
        assert result.words == frozenset({"食べる"})


class TestContract:
    def test_format_keys_enumeration(self):
        assert FORMAT_KEYS == ("jpdb", "migaku_json", "migaku_legacy", "ankimorphs", "migaku_csv", "generic")

    def test_result_words_are_frozenset(self, tmp_path):
        result = parse_known_words_file(_write(tmp_path, "words.txt", "犬\n"))
        assert isinstance(result, KnownWordsImportResult)
        assert isinstance(result.words, frozenset)
