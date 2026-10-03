"""yue card render hooks (spec F.1).

Each hook returns LOGICAL ``anki_fields`` keys; EpisodeProcessor phase 5 merges
them into ``extra_fields`` and ``anki_note_builder`` maps key -> Anki field name.
An unmapped key is skipped by the existing empty-name rule, so every hook field
is opt-in exactly like frequency/pitch/expression_audio.

``ZhMeasureWordHook`` is imported UNCHANGED (R32), built traditional-first
because that is the script every yue card is in: CC-CEDICT-Canto writes
classifiers inline as ``CL:套[tou3]`` in 2,458 of its 166,267 rows (measured
2026-09-20) and CC-Canto in none, so the field fills only when the
CC-CEDICT-Canto slot is the hit -- and the bracketed reading there is Mandarin
pinyin, which that hook already strips. Importing it pulls ``zh.reading`` and
``zh.variants``, whose engine imports are function-local, so no jieba, pypinyin
or opencc is loaded (pinned by test_yue_imports_no_zh_engine).

The classifier itself is Mandarin data too, so :class:`YueMeasureWordHook`
wraps that hook and swaps the two Mandarin-only classifiers for the ones Hong
Kong Cantonese uses with the same nouns.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from anki_miner.languages.yue.reading import jyutping_syllables
from anki_miner.languages.zh.render import ZhMeasureWordHook

if TYPE_CHECKING:  # annotation-only: keeps profile.py's resource_catalog import out of the runtime path
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

#: Six tones, six colours. Cantonese has no single community convention the way
#: Mandarin does, so this is a default in profile data rather than a setting:
#: 1 red, 2 orange, 3 green, 4 blue, 5 purple, 6 grey. Tone 0 (a syllable with
#: no digit, which only imported data produces) takes the neutral grey. The five
#: shared hues are zh's, note-type token and both-backgrounds fallback alike (see
#: the note on ``zh.render._TONE_COLORS``); purple joins them there.
_TONE_COLORS = {
    1: "var(--amn-tone-red, #e75353)",
    2: "var(--amn-tone-orange, #be7500)",
    3: "var(--amn-tone-green, #199a39)",
    4: "var(--amn-tone-blue, #4286e5)",
    5: "var(--amn-tone-purple, #a66dd2)",
    6: "var(--amn-tone-grey, #868686)",
}
_NEUTRAL = _TONE_COLORS[6]

#: CC-CEDICT's ``CL:`` classifier -> the one Hong Kong Cantonese uses instead.
#: CL:家 heads 56 CC-CEDICT-Canto rows (公司 銀行 超市 餐館 郵局), which Hong
#: Kong counts with 間; CL:臺/台 heads 63 (電腦 空調 計算機 錄音機), counted
#: with 部 (measured 2026-09-28). A few of those 63 fit 部 no better (話劇, 秤).
#: Every other classifier passes through unchanged.
_CANTONESE_CLASSIFIERS = {"家": "間", "臺": "部", "台": "部"}


class YueMeasureWordHook:
    """``ZhMeasureWordHook``, traditional-first, with Mandarin-only classifiers swapped.

    A thin wrapper rather than a subclass (R32): the zh hook picks the
    classifier and this maps the two that Cantonese does not use with those
    nouns. zh cards keep the CC-CEDICT classifier unchanged.
    """

    def __init__(self) -> None:
        self._zh = ZhMeasureWordHook(prefer="traditional")

    def field_names(self) -> tuple[str, ...]:
        return self._zh.field_names()

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        rendered = self._zh.render(word, config=config)
        return {key: _CANTONESE_CLASSIFIERS.get(value, value) for key, value in rendered.items()}


class YueJyutpingHook:
    """Jyutping for the extra field, tone-coloured when the config asks for it.

    ``config.reading_tone_color`` is the language-scoped field zh added; this
    hook is its only consumer for yue, so it must reach the setting or the
    setting does nothing here. Off, the field carries the same plain jyutping
    ``word_jyutping`` puts in the reading field.

    Inline ``style`` on purpose: the card must carry its own styling, never a
    note-type-global stylesheet (same rule as the glossary style block). Both
    branches escape -- this is an HTML field either way, and real jyutping has
    nothing to escape, so the off branch stays character-identical to the plain
    reading.

    Syllables are joined with a SPACE, not concatenated: jyutping is a
    romanisation whose word boundaries are the spaces, and the coloured field
    has to read like the plain one beside it.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("expression_jyutping",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        syllables = jyutping_syllables(getattr(word, "mined_form", "") or "")
        if not syllables:
            return {}
        if not config.reading_tone_color:
            return {"expression_jyutping": " ".join(html.escape(text) for text, _tone in syllables)}
        spans = " ".join(
            f'<span style="color:{_TONE_COLORS.get(tone, _NEUTRAL)}">{html.escape(text)}</span>'
            for text, tone in syllables
        )
        return {"expression_jyutping": spans}


YUE_RENDER_HOOKS: tuple[CardRenderHook, ...] = (YueMeasureWordHook(), YueJyutpingHook())
