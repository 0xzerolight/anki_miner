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
    ZH_RENDER_HOOKS,
    ZhMeasureWordHook,
    ZhToneColorHook,
    ZhTraditionalHook,
)

TONE_ON = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=True)
TONE_OFF = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=False)


def _word(mined_form="银行", definition_html=""):
    return SimpleNamespace(mined_form=mined_form, definition_html=definition_html)


def test_hook_field_names_are_logical_keys():
    assert [name for hook in ZH_RENDER_HOOKS for name in hook.field_names()] == [
        "measure_word",
        "expression_traditional",
        "expression_pinyin",
    ]


@pytest.mark.parametrize(
    ("gloss", "expected"),
    [
        ("bank; CL:家[jia1],個|个[ge4]", "家"),
        ("<li>CL:個|个[ge4]</li>", "個/个"),
        ("no classifier here", None),
    ],
)
def test_measure_word_parses_cc_cedict_cl_gloss(gloss, expected):
    out = ZhMeasureWordHook().render(_word(definition_html=gloss), config=TONE_ON)
    assert out == ({"measure_word": expected} if expected else {})


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
    assert "color:#e08a00" in html_out and "color:#8a8a8a" in html_out
    assert "háng&lt;" in html_out
    assert "class=" not in html_out  # no note-type-global CSS dependency


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
