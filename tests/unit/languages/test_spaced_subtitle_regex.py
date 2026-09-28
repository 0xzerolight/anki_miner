"""Latin SDH regex defaults (S10): they compile under the ReDoS screen and strip the SDH shapes."""

from __future__ import annotations

import itertools

import pytest

from anki_miner.gui.widgets.panels.sentences_settings_panel import SUBTITLE_REGEX_PRESETS
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


NORDIC_LINES = [
    ("-Kom hit.", "Kom hit."),
    ("- Kom hit.", "Kom hit."),
    ("Hej. -Kom hit.", "Hej. Kom hit."),
    ("-5 grader ute.", "-5 grader ute."),
    ("Det var -10 igår. -20 i natt.", "Det var -10 igår. -20 i natt."),
    ("Ett e-postmeddelande.", "Ett e-postmeddelande."),
    # Spanish opens a question or an exclamation with ¿ / ¡, right after the unspaced dash.
    ("-¿Vienes a cenar?", "¿Vienes a cenar?"),
    ("-No puedo. -¡Qué pena!", "No puedo. ¡Qué pena!"),
    ("- ¿Vienes?", "¿Vienes?"),
]


@pytest.mark.parametrize(("cue", "expected"), NORDIC_LINES)
def test_the_nordic_dash_takes_an_unspaced_letter_and_leaves_a_negative_number(cue, expected):
    assert compile_subtitle_regex_filter(script.NORDIC_DIALOGUE_DASH_PATTERN, "").sub("", cue) == expected


def test_the_nordic_dash_is_the_shipped_norwegian_pattern():
    """nb shipped it first; sv and da consume the same constant, so it lives in the shared module."""
    from anki_miner.languages.nb.morphology import NB_DIALOGUE_DASH_PATTERN, NB_SUBTITLE_REGEX

    assert NB_DIALOGUE_DASH_PATTERN is script.NORDIC_DIALOGUE_DASH_PATTERN
    assert NB_SUBTITLE_REGEX.endswith(script.NORDIC_DIALOGUE_DASH_PATTERN)
    compile_subtitle_regex_filter(script.NORDIC_DIALOGUE_DASH_PATTERN, "")  # ReDoS screen


#: Two-speaker cues written with the unspaced dash (the Spanish subtitling norm; es/pt/pl/lt probes).
UNSPACED_DASH_CUES = [
    ("es", "-¿Vienes a cenar? -No puedo, tengo que trabajar.", "¿Vienes a cenar? No puedo, tengo que trabajar."),
    ("pt", "-Você vem jantar? -Não posso.", "Você vem jantar? Não posso."),
    ("pl", "-Chodź tutaj! -Już idę.", "Chodź tutaj! Już idę."),
    ("lt", "-Eik čia. -Jau einu.", "Eik čia. Jau einu."),
]


@pytest.mark.parametrize(("code", "cue", "expected"), UNSPACED_DASH_CUES)
def test_the_first_visit_filter_strips_an_unspaced_dialogue_dash(code, cue, expected):
    from anki_miner.languages.registry import get_profile

    pattern = get_profile(code).scoped_defaults["subtitle_regex_filter"]
    assert isinstance(pattern, str)
    assert compile_subtitle_regex_filter(pattern, "").sub("", cue) == expected


def test_the_latin_unspaced_dash_default_swaps_only_the_dash_rule():
    parts = (*PARTS[:-1], script.NORDIC_DIALOGUE_DASH_PATTERN)
    assert "|".join(parts) == script.LATIN_UNSPACED_DASH_SUBTITLE_REGEX
    compile_subtitle_regex_filter(script.LATIN_UNSPACED_DASH_SUBTITLE_REGEX, "")  # ReDoS screen
