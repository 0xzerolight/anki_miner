"""zh card render hooks (spec 9.1).

Each hook returns LOGICAL ``anki_fields`` keys; EpisodeProcessor phase 5 merges
them into ``extra_fields`` and ``anki_note_builder`` maps key -> Anki field name.
An unmapped key is skipped by the existing empty-name rule, so every hook field
is opt-in exactly like frequency/pitch/expression_audio.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.zh.reading import pinyin_syllables
from anki_miner.languages.zh.variants import to_traditional

if TYPE_CHECKING:  # annotation-only: keeps profile.py's resource_catalog import out of the runtime path
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

# CC-CEDICT writes classifiers inline in the gloss: "CL:家[jia1],個|个[ge4]".
# Capture the first group, stop at the first separator or tag boundary.
_CL_RE = re.compile(r"CL\s*:\s*([^\s;,<]+)")
# 1 red / 2 orange / 3 green / 4 blue / 5 grey (surveyed convention, spec 9.1).
# Each hue's lightness sits in the one band that clears 3.5:1 on BOTH a stock
# white Anki card and Anki night mode (#2f2f31): an inline colour cannot adapt
# to the theme, and 4.5:1 on white would force under 3:1 on night mode.
_TONE_COLORS = {1: "#e75353", 2: "#be7500", 3: "#199a39", 4: "#4286e5", 5: "#868686"}


class ZhMeasureWordHook:
    """Measure word / classifier, best-effort from the fetched CC-CEDICT gloss.

    CC-CEDICT writes a two-script classifier as ``trad|simp`` (``CL:輛|辆``), so
    the half the card shows follows ``config.script_variant`` — printing both
    put a traditional glyph on a simplified learner's card and read as two
    classifiers. The halves are PICKED, never converted: ``to_script`` does not
    fold 隻 to 只, and the dictionary already supplies the pair. A front-derived
    choice covers a config with no script variant (yue, which is traditional).

    First classifier only, deliberately: a word with several (``CL:部,片,張|张``)
    gets the one CC-CEDICT lists first, not a list the field cannot hold.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("measure_word",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        match = _CL_RE.search(getattr(word, "definition_html", "") or "")
        if not match:
            return {}
        halves = [part.split("[")[0].strip() for part in match.group(1).split("|")]
        halves = [half for half in halves if half]
        if not halves:
            return {}
        if len(halves) == 1:
            return {"measure_word": halves[0]}
        return {"measure_word": halves[-1] if _wants_simplified(word, config) else halves[0]}


def _wants_simplified(word: Any, config: AnkiMinerConfig) -> bool:
    """Whether the card's script is simplified, from the setting or the front.

    zh always carries a variant (its scoped default is "simplified"), so the
    front branch is for a config that has none — yue's, whose cards are
    traditional. Only positive evidence flips it: a front with a traditional
    spelling of its own (汽车 -> 汽車) is simplified text. A script-invariant
    front (狗, 朋友, 睇) proves nothing and stays traditional, and so does every
    front where ``to_traditional`` returns its input because OpenCC is absent —
    the yue extra installs none.
    """
    variant = getattr(config, "script_variant", "")
    if variant:
        return variant == "simplified"
    front = getattr(word, "mined_form", "") or ""
    return to_traditional(front) != front


class ZhTraditionalHook:
    """Traditional-variant field; omitted when the form is script-invariant.

    ``to_traditional`` returns its input UNCHANGED both when OpenCC is missing
    and when the conversion raises, so "output == input" is the only signal for
    "no variant" there is — emitting it anyway would put a simplified spelling
    in the traditional field on every machine without OpenCC.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("expression_traditional",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # script_variant selects the CARD FRONT, not this extra field
        form = getattr(word, "mined_form", "") or ""
        traditional = to_traditional(form) if form else ""
        return {"expression_traditional": traditional} if traditional and traditional != form else {}


class ZhToneColorHook:
    """Pinyin for the extra field, tone-coloured when the config asks for it.

    ``config.reading_tone_color`` is the language-scoped field 2A.11 added; this
    hook is its only consumer, so it must reach the setting or the setting does
    nothing. Off, the field carries the same plain pinyin ``word_pinyin`` puts
    in the reading field.

    Inline ``style`` on purpose: the card must carry its own styling, never a
    note-type-global stylesheet (same rule as the glossary style block). Both
    branches escape — this is an HTML field either way, and real pinyin has
    nothing to escape, so the off branch stays character-identical to the plain
    reading.

    Syllables are joined with a SPACE, not concatenated: pinyin is a
    romanisation whose word boundaries are the spaces (yín háng, not yínháng),
    and the coloured field has to read like the plain one beside it.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("expression_pinyin",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        syllables = pinyin_syllables(getattr(word, "mined_form", "") or "")
        if not syllables:
            return {}
        if not config.reading_tone_color:
            return {"expression_pinyin": " ".join(html.escape(text) for text, _ in syllables)}
        spans = " ".join(
            f'<span style="color:{_TONE_COLORS.get(tone, _TONE_COLORS[5])}">{html.escape(text)}</span>'
            for text, tone in syllables
        )
        return {"expression_pinyin": spans}


ZH_RENDER_HOOKS: tuple[CardRenderHook, ...] = (ZhMeasureWordHook(), ZhTraditionalHook(), ZhToneColorHook())
