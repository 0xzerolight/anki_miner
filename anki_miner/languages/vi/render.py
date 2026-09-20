"""Vietnamese card render hooks: the Hán Việt (Sino-Vietnamese) characters (spec C.4).

The source is the definition the card already carries: phase 5 stashes it on the
word as ``definition_html`` (``EpisodeProcessor._apply_render_hooks``), the way
``ZhMeasureWordHook`` reads its ``CL:`` marker. wty-vi-en writes the etymology
as "Sino-Vietnamese word from 和平." (12,520 rows of revision 2026.09.19); the
first CJK run after that phrase is the field. A native word, or one Wiktionary
calls a "Non-Sino-Vietnamese reading of Chinese X", gets no field. A dictionary
that renders no etymology leaves it blank. The mapped Anki field name is the
on/off switch, like every hook field.
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
_HANZI = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f"
_SINO_VIETNAMESE = re.compile(f"Sino-Vietnamese word from ([{_HANZI}]+)")


class HanVietHook:
    """``hanviet``: the hanzi a Sino-Vietnamese word is read from (bác sĩ → 博士)."""

    def field_names(self) -> tuple[str, ...]:
        return ("hanviet",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates it
        match = _SINO_VIETNAMESE.search(str(getattr(word, "definition_html", "") or ""))
        return {"hanviet": match.group(1)} if match else {}


VI_RENDER_HOOKS: tuple[CardRenderHook, ...] = (HanVietHook(),)
