"""The playlist picker's `1-10,15,20-` range expression."""

from __future__ import annotations

import pytest

from anki_miner.gui.utils.playlist_selection import parse_index_selection


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1", {1}),
        ("1,3,5", {1, 3, 5}),
        ("1-3", {1, 2, 3}),
        ("1-3,7", {1, 2, 3, 7}),
        ("8-", {8, 9, 10}),
        ("-3", {1, 2, 3}),
        (" 1 - 3 , 7 ", {1, 2, 3, 7}),
        ("3-1", {1, 2, 3}),
        ("1-99", {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}),
        ("", set()),
        ("1,,3", {1, 3}),
        ("2,2", {2}),
    ],
)
def test_valid_expressions(spec: str, expected: set[int]) -> None:
    assert parse_index_selection(spec, total=10) == expected


@pytest.mark.parametrize("spec", ["0", "abc", "1-a", "1--3", "12", "-", "1.5"])
def test_invalid_expressions_raise(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_index_selection(spec, total=10)


def test_the_message_names_the_offending_part() -> None:
    with pytest.raises(ValueError, match="abc"):
        parse_index_selection("1,abc", total=10)
