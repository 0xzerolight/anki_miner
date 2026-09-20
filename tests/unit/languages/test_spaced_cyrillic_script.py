"""CyrillicScript (plan D11): the ingestion/mining gate is "has a Cyrillic letter"; no toggles."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.script import CyrillicScript, is_cyrillic_letter

TITLO = "\N{COMBINING CYRILLIC TITLO}"


@pytest.mark.parametrize(
    "text",
    [
        "книга",
        "Ёлка",
        "читала",
        "їжак",  # uk
        "ђак",  # sr
        "ъгъл",  # bg
        "iPhone и Android",
    ],
)
def test_a_line_with_a_cyrillic_letter_passes(text):
    assert CyrillicScript().contains_target_script(text)


@pytest.mark.parametrize("text", ["book", "βιβλίο", "日本語", "12345", "...!?", "", "№ 5"])
def test_a_line_without_one_does_not(text):
    assert not CyrillicScript().contains_target_script(text)


def test_only_letters_count():
    assert is_cyrillic_letter("ж") and is_cyrillic_letter("Ё")
    assert not is_cyrillic_letter(TITLO)  # a combining mark, not a letter
    assert not is_cyrillic_letter("a")


def test_no_script_toggles():
    script = CyrillicScript()
    assert script.filter_options() == ()
    assert script.matches("anything", "книга") is False
