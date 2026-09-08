"""The playlist picker's `1-10,15,20-` range expression."""

from __future__ import annotations

import pytest

from anki_miner.gui.utils.playlist_selection import SelectionError, parse_index_selection


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


def test_a_later_page_selects_by_the_numbers_shown() -> None:
    assert parse_index_selection("502-503,505", total=5, first=501) == {502, 503, 505}
    assert parse_index_selection("504-", total=5, first=501) == {504, 505}
    assert parse_index_selection("-502", total=5, first=501) == {501, 502}
    with pytest.raises(ValueError):
        parse_index_selection("3", total=5, first=501)


def test_colon_is_an_alias_for_dash() -> None:
    assert parse_index_selection("1:20,25", total=30) == parse_index_selection("1-20,25", total=30)
    assert parse_index_selection("8:", total=10) == {8, 9, 10}


@pytest.mark.parametrize(
    ("spec", "kind", "value"),
    [
        ("abc", "not_a_range", "abc"),
        ("1--3", "not_a_range", "1--3"),
        ("12", "no_such_video", "12"),
        ("-", "open_range", "-"),
        (":", "open_range", ":"),
        ("0-3", "from_one", "0-3"),
    ],
)
def test_errors_carry_a_kind_and_the_offending_part(spec: str, kind: str, value: str) -> None:
    with pytest.raises(SelectionError) as info:
        parse_index_selection(spec, total=10)
    assert (info.value.kind, info.value.value) == (kind, value)
