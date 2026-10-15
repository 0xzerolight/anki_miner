"""WP-34 (ruling R10): fixed-column word exports for Manage Known Words and a run's results."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.models.processing import NotMinedReason, NotMinedReport
from anki_miner.services.known_word_db import KnownWordRow
from anki_miner.services.word_export import (
    KNOWN_WORDS_COLUMNS,
    RUN_WORDS_COLUMNS,
    run_word_rows,
    write_known_words_csv,
    write_run_words_csv,
    write_word_list,
)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


def test_the_columns_are_fixed():
    assert KNOWN_WORDS_COLUMNS == ("word", "source", "added_at_utc")
    assert RUN_WORDS_COLUMNS == ("word", "status", "reason")


def test_known_words_csv(tmp_path):
    rows = [KnownWordRow("猫", "anki", "2026-10-01 10:00:00"), KnownWordRow("犬", "user", "2026-10-02 11:00:00")]
    out = tmp_path / "words.csv"

    assert write_known_words_csv(out, rows) == 2

    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert _lines(out) == ["word,source,added_at_utc", "猫,anki,2026-10-01 10:00:00", "犬,user,2026-10-02 11:00:00"]


def test_a_comma_in_a_word_is_quoted(tmp_path):
    out = tmp_path / "words.csv"

    write_known_words_csv(out, [KnownWordRow("a,b", "user", "")])

    assert _lines(out)[1] == '"a,b",user,'


def test_word_list_is_one_word_per_line_without_a_bom(tmp_path):
    out = tmp_path / "words.txt"

    assert write_word_list(out, ["猫", "犬"]) == 2

    assert out.read_bytes() == "猫\n犬\n".encode()


def test_an_empty_word_list_is_an_empty_file(tmp_path):
    out = tmp_path / "words.txt"

    assert write_word_list(out, []) == 0
    assert out.read_bytes() == b""


def test_run_rows_list_mined_words_then_each_reason_in_pipeline_order():
    report = NotMinedReport(
        {
            NotMinedReason.NO_DEFINITION: frozenset({"鳥", "犬"}),
            NotMinedReason.KNOWN: frozenset({"猫", "鳥"}),
            NotMinedReason.I_PLUS_ONE: frozenset({"走る"}),
        },
        mined=frozenset({"走る", "泳ぐ"}),
    )

    assert run_word_rows(report) == [
        ("泳ぐ", "mined", ""),
        ("走る", "mined", ""),
        ("猫", "not_mined", "known"),
        ("鳥", "not_mined", "known"),
        ("犬", "not_mined", "no_definition"),
    ]


def test_run_words_csv(tmp_path):
    out = tmp_path / "run.csv"
    report = NotMinedReport({NotMinedReason.KNOWN: frozenset({"猫"})}, mined=frozenset({"走る"}))

    assert write_run_words_csv(out, report) == 2

    assert _lines(out) == ["word,status,reason", "走る,mined,", "猫,not_mined,known"]


def test_a_failed_write_leaves_no_file(tmp_path):
    out = tmp_path / "missing" / "words.csv"

    with pytest.raises(OSError):
        write_known_words_csv(out, [KnownWordRow("猫", "anki", "")])

    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
