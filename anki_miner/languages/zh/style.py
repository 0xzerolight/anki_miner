"""zh content typography DATA (spec 9.1) — face candidates and the wrap.

Data only. The Qt-level plumbing that consumes ``ZH_CONTENT_STYLE``
(``gui/utils/content_text.py``, the QSS selectors, the font-database probe)
belongs to Stage 2B; this module exists because ``build_profile()`` cannot name
a ``content_style`` whose value has no source.

Nothing here imports ``anki_miner.gui``: ``languages`` carries no import-time
edge into ``gui`` (pinned by
``test_languages_package_carries_no_import_time_gui_edge``).
"""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle

__all__ = ["ZH_CONTENT_STYLE", "ZH_FONT_FAMILIES", "zh_cjk_wrap"]

#: Installed Han faces in preference order, Simplified leading: the profile's
#: scoped ``script_variant`` default is "simplified", and an SC face renders a
#: traditional string acceptably while the reverse drops or mis-shapes
#: simplified glyphs on several of these. Windows first, then macOS, then the
#: usual Linux packages; the TC-first faces are the tail, not the head. None is
#: required to exist — Qt walks the list and takes the first one installed.
ZH_FONT_FAMILIES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "PingFang TC",
    "Noto Sans CJK TC",
    "Microsoft JhengHei",
)


def zh_cjk_wrap(text: str) -> str:
    """Return *text* unchanged — Chinese needs no phrase-wrap transform.

    The ja wrapper exists because breaking 行きま/しょう mid-conjugation is
    wrong, so BudouX phrase chunks are stitched with WORD JOINER. Chinese has
    no inflected tail to protect: a Han run is UAX #14 class ID, a break
    between any two characters is correct typography, and that is already Qt's
    default. Identity rather than ``None`` so ``content_phrase_wrap`` can call
    ``style.wrap`` unconditionally for every language.
    """
    return text


ZH_CONTENT_STYLE = ContentTextStyle(font_role="zh", families=ZH_FONT_FAMILIES, wrap=zh_cjk_wrap)
