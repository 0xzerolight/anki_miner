"""yue jyutping readings and the tone-colour card hook (real engine)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.yue.reading import YueReadingSupport, jyutping_syllables, word_jyutping
from anki_miner.languages.yue.render import _TONE_COLORS, YUE_RENDER_HOOKS, YueJyutpingHook
from tests.unit.languages.test_zh_render import CARD_BACKGROUNDS, MIN_CONTRAST, wcag_contrast

FIXTURE = Path(__file__).parents[2] / "fixtures" / "yue" / "jyutping.jsonl"
ROWS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["word"])
def test_the_reading_matches_the_recorded_engine_output(row):
    assert word_jyutping(row["word"]) == row["jyutping"]


def test_an_out_of_vocabulary_word_reads_empty_not_none():
    assert word_jyutping("Netflix") == ""
    assert word_jyutping("") == ""


def test_the_reading_support_reads_the_lemma_not_the_surface():
    token = SimpleNamespace(surface="今 日", feature=SimpleNamespace(lemma="今日"))
    assert YueReadingSupport().word_reading(token) == "gam1 jat6"


def test_syllables_carry_their_tone_digit():
    assert jyutping_syllables("睇咗") == [("tai2", 2), ("zo2", 2)]
    assert jyutping_syllables("Netflix") == []


def test_the_hook_emits_plain_jyutping_when_tone_colour_is_off():
    config = replace(AnkiMinerConfig(), reading_tone_color=False)
    word = SimpleNamespace(mined_form="睇咗")
    assert YueJyutpingHook().render(word, config=config) == {"expression_jyutping": "tai2 zo2"}


def test_the_hook_colours_six_tones():
    config = replace(AnkiMinerConfig(), reading_tone_color=True)
    rendered = YueJyutpingHook().render(SimpleNamespace(mined_form="香港"), config=config)["expression_jyutping"]
    assert rendered == '<span style="color:#e75353">hoeng1</span> <span style="color:#be7500">gong2</span>'


@pytest.mark.parametrize("background", CARD_BACKGROUNDS)
@pytest.mark.parametrize(("tone", "color"), sorted(_TONE_COLORS.items()))
def test_every_tone_colour_is_readable_on_both_card_backgrounds(tone, color, background):
    """Six tones held to the same band as zh's five."""
    assert wcag_contrast(color, background) >= MIN_CONTRAST, (tone, color, background)


def test_the_hook_emits_nothing_for_a_word_with_no_reading():
    config = replace(AnkiMinerConfig(), reading_tone_color=True)
    assert YueJyutpingHook().render(SimpleNamespace(mined_form="Netflix"), config=config) == {}
    assert YueJyutpingHook().render(SimpleNamespace(mined_form=""), config=config) == {}


def test_the_measure_word_hook_is_the_zh_one_unchanged():
    from anki_miner.languages.zh.render import ZhMeasureWordHook

    assert [type(hook) for hook in YUE_RENDER_HOOKS] == [ZhMeasureWordHook, YueJyutpingHook]
    word = SimpleNamespace(definition_html="to watch/CL:套[tou3]", mined_form="戲")
    assert YUE_RENDER_HOOKS[0].render(word, config=AnkiMinerConfig()) == {"measure_word": "套"}


def test_a_two_script_classifier_takes_the_traditional_half_on_a_yue_card():
    """yue never sets script_variant, so the classifier follows the card front."""
    config = replace(AnkiMinerConfig(), script_variant="")
    word = SimpleNamespace(definition_html="pair; CL:對|对[dui4]", mined_form="對")
    assert YUE_RENDER_HOOKS[0].render(word, config=config) == {"measure_word": "對"}


def test_a_script_invariant_front_takes_the_traditional_half_too():
    """狗 is spelt the same in both scripts; yue's own script is the only evidence left.

    The yue extra ships no OpenCC, so this is also the shape of every yue card
    on a default install.
    """
    config = replace(AnkiMinerConfig(), script_variant="")
    word = SimpleNamespace(definition_html="dog; CL:隻|只[zek3],條|条[tiu4]", mined_form="狗")
    assert YUE_RENDER_HOOKS[0].render(word, config=config) == {"measure_word": "隻"}


def test_every_hook_field_name_is_declared_once():
    names = [name for hook in YUE_RENDER_HOOKS for name in hook.field_names()]
    assert names == ["measure_word", "expression_jyutping"]
