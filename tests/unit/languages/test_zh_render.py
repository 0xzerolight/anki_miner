"""zh render hooks: measure word, traditional variant, tone-coloured pinyin.

``render`` takes the config keyword-only. Without it the scoped
``reading_tone_color`` field added by 2A.11 was structurally unreachable — the
hook had nothing to gate on, so the setting could never do anything.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.zh.render import (
    _TONE_COLORS,
    ZH_RENDER_HOOKS,
    ZhMeasureWordHook,
    ZhToneColorHook,
    ZhTraditionalHook,
)

TONE_ON = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=True)
TONE_OFF = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=False)

#: The two backgrounds an Anki card is actually read on: the stock white card
#: and Anki's own night mode. Shared with the yue palette test.
CARD_BACKGROUNDS = ("#ffffff", "#2f2f31")
MIN_CONTRAST = 3.5


def wcag_contrast(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two opaque sRGB hex colours."""

    def luminance(color: str) -> float:
        channels = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _word(mined_form="银行", definition_html=""):
    return SimpleNamespace(mined_form=mined_form, definition_html=definition_html)


def test_hook_field_names_are_logical_keys():
    assert [name for hook in ZH_RENDER_HOOKS for name in hook.field_names()] == [
        "measure_word",
        "expression_traditional",
        "expression_pinyin",
    ]


#: A simplified front is the one case OpenCC has to place: every other front
#: falls to the traditional half, which is where a missing OpenCC lands anyway.
_NEEDS_OPENCC = frozenset({"个人"})


@pytest.mark.parametrize(
    ("gloss", "variant", "front", "expected"),
    [
        ("bank; CL:家[jia1],個|个[ge4]", "simplified", "银行", "家"),
        ("car; CL:輛|辆[liang4]", "simplified", "汽车", "辆"),
        ("car; CL:輛|辆[liang4]", "traditional", "汽車", "輛"),
        ("<li>CL:個|个[ge4]</li>", "", "个人", "个"),
        ("<li>CL:個|个[ge4]</li>", "", "個人", "個"),
        ("dog; CL:隻|只[zhi1],條|条[tiao2]", "", "狗", "隻"),
        ("friend; CL:個|个[ge4]", "", "朋友", "個"),
        ("only one; CL:隻|只[zhi1]", "simplified", "鸟", "只"),
        ("to watch; CL:套[tou3]", "", "戲", "套"),
        ("no classifier here", "simplified", "银行", None),
    ],
)
def test_measure_word_parses_cc_cedict_cl_gloss(gloss, variant, front, expected):
    """CC-CEDICT writes a classifier ``trad|simp``; the card shows its own script."""
    if not variant and front in _NEEDS_OPENCC:
        pytest.importorskip("opencc")
    config = dataclasses.replace(AnkiMinerConfig(), script_variant=variant)
    out = ZhMeasureWordHook().render(_word(front, gloss), config=config)
    assert out == ({"measure_word": expected} if expected else {})


def test_a_front_opencc_cannot_place_takes_the_traditional_half(monkeypatch):
    """No OpenCC is the yue install, and an unplaceable front leaves only yue's script."""
    monkeypatch.setattr("anki_miner.languages.zh.render.to_traditional", lambda text: text)
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    out = ZhMeasureWordHook().render(_word("汽车", "car; CL:輛|辆[liang4]"), config=config)
    assert out == {"measure_word": "輛"}


def test_traditional_hook_emits_only_a_real_variant(monkeypatch):
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.to_traditional",
        lambda text: {"银行": "銀行"}.get(text, text),
    )
    assert ZhTraditionalHook().render(_word("银行"), config=TONE_ON) == {"expression_traditional": "銀行"}
    assert ZhTraditionalHook().render(_word("你好"), config=TONE_ON) == {}


def test_tone_colour_spans_are_self_contained_and_escaped(monkeypatch):
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.pinyin_syllables",
        lambda text: [("yín", 2), ("háng<", 5)],
    )
    html_out = ZhToneColorHook().render(_word("银行"), config=TONE_ON)["expression_pinyin"]
    assert html_out.count("<span style=") == 2
    assert "color:#be7500" in html_out and "color:#868686" in html_out
    assert "háng&lt;" in html_out
    assert "class=" not in html_out  # no note-type-global CSS dependency


@pytest.mark.parametrize("background", CARD_BACKGROUNDS)
@pytest.mark.parametrize(("tone", "color"), sorted(_TONE_COLORS.items()))
def test_every_tone_colour_is_readable_on_both_card_backgrounds(tone, color, background):
    """One inline colour, two backgrounds: the palette lives in the band that clears both."""
    assert wcag_contrast(color, background) >= MIN_CONTRAST, (tone, color, background)


def test_tone_colour_keeps_the_syllable_separator(monkeypatch):
    """The coloured reading must read like the plain one: yín háng, not yínháng."""
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.pinyin_syllables",
        lambda text: [("yín", 2), ("háng", 2)],
    )
    html_out = ZhToneColorHook().render(_word("银行"), config=TONE_ON)["expression_pinyin"]
    assert "</span> <span" in html_out
    assert "</span><span" not in html_out


def test_tone_colour_off_emits_plain_pinyin(monkeypatch):
    """The 2A.11 scoped field is what the hook gates on; off means no markup."""
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.pinyin_syllables",
        lambda text: [("yín", 2), ("háng", 2)],
    )
    assert ZhToneColorHook().render(_word("银行"), config=TONE_OFF) == {"expression_pinyin": "yín háng"}


def test_hooks_return_empty_dicts_rather_than_raising(monkeypatch):
    monkeypatch.setattr("anki_miner.languages.zh.render.pinyin_syllables", lambda text: [])
    assert ZhToneColorHook().render(_word(""), config=TONE_ON) == {}
    assert ZhToneColorHook().render(_word(""), config=TONE_OFF) == {}
    assert ZhMeasureWordHook().render(_word(), config=TONE_ON) == {}


def test_every_hook_takes_the_config_keyword_only():
    """A positional-config hook would silently miss the gate on every language."""
    import inspect

    for hook in ZH_RENDER_HOOKS:
        parameter = inspect.signature(hook.render).parameters["config"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, type(hook).__name__
