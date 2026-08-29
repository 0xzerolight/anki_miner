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
_TONE_COLORS = {1: "#e02020", 2: "#e08a00", 3: "#1a9e3a", 4: "#1f6fe0", 5: "#8a8a8a"}


class ZhMeasureWordHook:
    """Measure word / classifier, best-effort from the fetched CC-CEDICT gloss."""

    def field_names(self) -> tuple[str, ...]:
        return ("measure_word",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the classifier is always emitted when the gloss carries one
        match = _CL_RE.search(getattr(word, "definition_html", "") or "")
        if not match:
            return {}
        forms: list[str] = []
        for part in match.group(1).split("|"):
            form = part.split("[")[0].strip()
            if form and form not in forms:
                forms.append(form)
        return {"measure_word": "/".join(forms)} if forms else {}


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
