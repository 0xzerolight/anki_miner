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
    _script_is_simplified,
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


def _word(mined_form="银行", definition_html="", sentence=""):
    return SimpleNamespace(mined_form=mined_form, definition_html=definition_html, sentence=sentence)


def test_hook_field_names_are_logical_keys():
    assert [name for hook in ZH_RENDER_HOOKS for name in hook.field_names()] == [
        "measure_word",
        "expression_traditional",
        "expression_pinyin",
    ]


#: A script-distinct front is the only case OpenCC has to place: a front spelt
#: the same in both scripts falls through to the sentence, then to the default,
#: which is where a missing OpenCC lands anyway.
_NEEDS_OPENCC = frozenset({"个人", "個人", "裏面"})


@pytest.mark.parametrize(
    ("gloss", "variant", "front", "expected"),
    [
        ("bank; CL:家[jia1],個|个[ge4]", "simplified", "银行", "家"),
        ("car; CL:輛|辆[liang4]", "simplified", "汽车", "辆"),
        ("car; CL:輛|辆[liang4]", "traditional", "汽車", "輛"),
        ("<li>CL:個|个[ge4]</li>", "", "个人", "个"),
        ("<li>CL:個|个[ge4]</li>", "", "個人", "個"),
        # 裏 is traditional but not the Taiwan spelling, so s2tw rewrites it to
        # 裡: only the simplifying test can place a mainland-traditional front.
        ("inside; CL:個|个[ge4]", "", "裏面", "個"),
        ("dog; CL:隻|只[zhi1],條|条[tiao2]", "", "狗", "只"),
        ("friend; CL:個|个[ge4]", "", "朋友", "个"),
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


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [("那只狗在门口等他。", "只"), ("那隻狗在門口等他。", "隻")],
)
def test_a_script_invariant_front_reads_the_sentence(sentence, expected):
    """狗 is spelt the same in both scripts; the line it came from is not."""
    pytest.importorskip("opencc")
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    word = _word("狗", "dog; CL:隻|只[zhi1],條|条[tiao2]", sentence=sentence)
    assert ZhMeasureWordHook().render(word, config=config) == {"measure_word": expected}


@pytest.mark.parametrize("word", ["显著", "著称", "专著", "执著", "论著", "土著", "原著", "编著"])
def test_a_simplified_sentence_holding_a_taiwan_variant_still_reads_as_simplified(word):
    """tw2s folds 著 -> 着, so a probe built on it called every one of these traditional."""
    pytest.importorskip("opencc")
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    sentence = f"那只狗的忠诚很{word}。"
    assert _script_is_simplified(sentence) is True
    hook_word = _word("狗", "dog; CL:隻|只[zhi1],條|条[tiao2]", sentence=sentence)
    assert ZhMeasureWordHook().render(hook_word, config=config) == {"measure_word": "只"}


@pytest.mark.parametrize(("prefer", "expected"), [("simplified", "只"), ("traditional", "隻")])
def test_a_front_and_sentence_with_no_script_fall_to_the_hook_default(prefer, expected):
    """Nothing about 狗 alone says which script the card is in; the hook's own default does."""
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    word = _word("狗", "dog; CL:隻|只[zhi1],條|条[tiao2]")
    assert ZhMeasureWordHook(prefer=prefer).render(word, config=config) == {"measure_word": expected}


@pytest.mark.parametrize(("prefer", "expected"), [("simplified", "辆"), ("traditional", "輛")])
def test_without_opencc_every_text_falls_to_the_hook_default(monkeypatch, prefer, expected):
    """No OpenCC is the default yue install: neither test can place front or sentence."""
    monkeypatch.setattr("anki_miner.languages.zh.render.is_traditional", lambda text: False)
    monkeypatch.setattr("anki_miner.languages.zh.render.to_traditional", lambda text: text)
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    word = _word("汽车", "car; CL:輛|辆[liang4]", sentence="这辆汽车很贵。")
    assert ZhMeasureWordHook(prefer=prefer).render(word, config=config) == {"measure_word": expected}


def test_traditional_hook_emits_only_a_real_variant(monkeypatch):
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.to_traditional",
        lambda text: {"银行": "銀行"}.get(text, text),
    )
    assert ZhTraditionalHook().render(_word("银行"), config=TONE_ON) == {"expression_traditional": "銀行"}
    assert ZhTraditionalHook().render(_word("你好"), config=TONE_ON) == {}


@pytest.mark.parametrize("front", ["裏面", "怎麽", "頭髮", "這裡"])
def test_a_traditional_front_gets_no_traditional_field(front):
    """Under As written the front keeps its own spelling; s2tw would re-spell it (裏面 -> 裡面)."""
    pytest.importorskip("opencc")
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    assert ZhTraditionalHook().render(_word(front), config=config) == {}


def test_a_simplified_front_still_gets_its_variant():
    pytest.importorskip("opencc")
    config = dataclasses.replace(AnkiMinerConfig(), script_variant="")
    assert ZhTraditionalHook().render(_word("里面"), config=config) == {"expression_traditional": "裡面"}


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


def test_tone_colour_paints_the_word_s_own_reading(monkeypatch):
    """The card's Pinyin field must agree with its Reading field, syllable for syllable."""
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.pinyin_syllables",
        lambda text: pytest.fail("the word already carries a reading"),
    )
    word = SimpleNamespace(mined_form="看得见", expression_reading="kàn de jiàn", definition_html="")
    html_out = ZhToneColorHook().render(word, config=TONE_ON)["expression_pinyin"]
    assert html_out.count("<span style=") == 3
    assert '<span style="color:#868686">de</span>' in html_out


def test_a_word_without_a_reading_still_paints_from_the_front(monkeypatch):
    """A word with no dictionary entry reaches the hook carrying no reading at all."""
    monkeypatch.setattr(
        "anki_miner.languages.zh.render.pinyin_syllables",
        lambda text: [("yín", 2), ("háng", 2)],
    )
    word = _word("银行")
    assert not hasattr(word, "expression_reading")
    html_out = ZhToneColorHook().render(word, config=TONE_ON)["expression_pinyin"]
    assert html_out == '<span style="color:#be7500">yín</span> <span style="color:#be7500">háng</span>'


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
