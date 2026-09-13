"""Latin SDH regex defaults (S10): they compile under the ReDoS screen and strip the SDH shapes."""

from __future__ import annotations

import itertools

import pytest

from anki_miner.gui.widgets.panels.filtering_settings_panel import SUBTITLE_REGEX_PRESETS
from anki_miner.languages._spaced import script
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

PARTS = (
    script.BRACKETS_PATTERN,
    script.PARENS_PATTERN,
    script.MUSIC_PATTERN,
    script.LATIN_SPEAKER_PATTERN,
    script.DIALOGUE_DASH_PATTERN,
)


def _clean(line: str) -> str:
    filtered = compile_subtitle_regex_filter(script.LATIN_SUBTITLE_REGEX, "").sub("", line)
    return " ".join(filtered.split())


def test_the_default_is_the_five_parts_in_order():
    assert "|".join(PARTS) == script.LATIN_SUBTITLE_REGEX


def test_every_part_compiles_alone_and_stacked_in_every_order():
    for part in PARTS:
        compile_subtitle_regex_filter(part, "")
    for order in itertools.permutations(PARTS):
        compile_subtitle_regex_filter("|".join(order), "")


def test_no_part_carries_an_inline_flag():
    assert not [p for p in PARTS if "(?" in p.replace("(?:", "").replace("(?<", "")]


@pytest.mark.parametrize(
    ("cue", "expected"),
    [
        ("[door slams] Get out!", "Get out!"),
        ("(laughs) That's funny.", "That's funny."),
        ("♪ Never gonna give you up ♪", "Never gonna give you up"),
        ("JOHN: Where were you?", "Where were you?"),
        ("DR. SMITH: Sit down.", "Sit down."),
        ("- Hi. - Hello.", "Hi. Hello."),
        ("- Hallo. - Hi.", "Hallo. Hi."),
        ("— Who's there?", "Who's there?"),
        ("sagte sie – wirklich!", "sagte sie – wirklich!"),
        ("I was — well — tired.", "I was — well — tired."),
        ("A well-known e-mail address.", "A well-known e-mail address."),
        ("Mr. Smith said: fine.", "Mr. Smith said: fine."),
    ],
)
def test_the_default_strips_sdh_and_keeps_dialogue(cue, expected):
    assert _clean(cue) == expected


def test_the_dialogue_dash_preset_is_offered_to_every_language():
    assert SUBTITLE_REGEX_PRESETS[-1] == ("Dialogue dash", script.DIALOGUE_DASH_PATTERN)
