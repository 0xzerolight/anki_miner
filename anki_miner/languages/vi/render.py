"""Vietnamese card render hooks: the Hán Việt (Sino-Vietnamese) characters (spec C.4).

The source is the definition the card already carries: phase 5 stashes it on the
word as ``definition_html`` (``EpisodeProcessor._apply_render_hooks``), the way
``ZhMeasureWordHook`` reads its ``CL:`` marker. wty-vi-en writes the etymology
as "Sino-Vietnamese word from 和平." (12,520 rows of revision 2026.09.19, every
one inside its row's Etymology section); the first CJK run after that phrase is
the field. Only the definition's first row is read, the one the card opens on
(the row rank in vi/keys.py puts the token's word class first): the definition
carries every homograph, and reading past the first row gave bố "father" 布 from
the burlap row, khi "when" 欺 and đồng 童 from the shaman row. A native first
row, or one Wiktionary calls a "Non-Sino-Vietnamese reading of Chinese X", gets
no field. A dictionary that renders no etymology leaves it blank. The mapped
Anki field name is the on/off switch, like every hook field.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.profile import CardFieldSpec

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

HANVIET_FIELD = CardFieldSpec(key="hanviet", capability="hanviet", placeholder="HanViet")

#: CJK Unified Ideographs, Extension A, Compatibility, and the supplementary planes (Ext B onward).
_HANZI = "㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f"
_SINO_VIETNAMESE = re.compile(f"Sino-Vietnamese word from ([{_HANZI}]+)")
#: Every dictionary row renders as one of these (yomitan_renderer); the definition lists them in rank order.
_ROW_START = '<li class="gloss-item"'


def _first_row(definition_html: str) -> str:
    """The definition's first row, or ``""`` when it renders none."""
    start = definition_html.find(_ROW_START)
    if start < 0:
        return ""
    end = definition_html.find(_ROW_START, start + len(_ROW_START))
    return definition_html[start:] if end < 0 else definition_html[start:end]


class HanVietHook:
    """``hanviet``: the hanzi a Sino-Vietnamese word is read from (bác sĩ → 博士)."""

    def field_names(self) -> tuple[str, ...]:
        return ("hanviet",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates it
        match = _SINO_VIETNAMESE.search(_first_row(str(getattr(word, "definition_html", "") or "")))
        return {"hanviet": match.group(1)} if match else {}


VI_RENDER_HOOKS: tuple[CardRenderHook, ...] = (HanVietHook(),)
