"""yue content typography DATA (spec F.1) -- face candidates, the wrap, the card tag.

Data only. Nothing here imports ``anki_miner.gui``: ``languages`` carries no
import-time edge into ``gui`` (pinned by
``test_languages_package_carries_no_import_time_gui_edge``).

Hong Kong faces lead, because HKSCS glyph coverage is what colloquial Cantonese
needs (嘅 哋 喺 啲) and a Taiwan-traditional face can miss them. ``writing_system``
turns on the S22 probe: if none of these families is installed for
``TraditionalChinese`` the profile has no bundled face to fall back to (a CJK
face is several MB, far outside the OFL budget, and the bundled NotoSansJP has
no HKSCS), so ``resolve_content_families`` logs one warning and Qt substitutes.
That tofu risk is recorded in the spec's risk table rather than paid for in
bundle size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anki_miner.languages.profile import ContentTextStyle

if TYPE_CHECKING:
    from anki_miner.config.config import AnkiMinerConfig

__all__ = ["YUE_CONTENT_STYLE", "YUE_FONT_FAMILIES", "yue_card_lang", "yue_cjk_wrap"]

#: Installed Han faces in preference order, Hong Kong first.
YUE_FONT_FAMILIES: tuple[str, ...] = (
    "PingFang HK",
    "Microsoft JhengHei UI",
    "Microsoft JhengHei",
    "Noto Sans CJK HK",
    "Noto Sans HK",
    "Source Han Sans HC",
    "Noto Sans CJK TC",
    "PingFang TC",
    "WenQuanYi Micro Hei",
)


def yue_cjk_wrap(text: str) -> str:
    """Return *text* unchanged -- Cantonese needs no phrase-wrap transform.

    The ja wrapper exists because breaking 行きま/しょう mid-conjugation is
    wrong. A Han run is UAX #14 class ID, a break between any two characters is
    correct typography, and that is already Qt's default. Identity rather than
    ``None`` so ``content_phrase_wrap`` can call ``style.wrap`` unconditionally
    for every language.
    """
    return text


def yue_card_lang(text: str, config: AnkiMinerConfig) -> str:
    """BCP-47 tag for the Cantonese text on a card: always traditional Chinese.

    Cantonese is written in traditional Han, and the profile code ``yue`` is no
    use to a reviewer's font fallback -- no CJK face declares it, so the tag
    that actually routes 骨 and 直 to Chinese shapes rather than Japanese ones
    is the script subtag. ``text`` and ``config`` are the one cross-language
    resolver signature and are unused here.
    """
    return "zh-Hant"


YUE_CONTENT_STYLE = ContentTextStyle(
    font_role="yue",
    families=YUE_FONT_FAMILIES,
    wrap=yue_cjk_wrap,
    direction="ltr",
    writing_system="TraditionalChinese",
    bundled_fallback="",
    card_lang=yue_card_lang,
)
