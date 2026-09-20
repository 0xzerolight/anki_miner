"""th content typography DATA (spec C.3) -- face candidates and the wrap.

Data only; nothing here imports ``anki_miner.gui`` (pinned by
``test_languages_package_carries_no_import_time_gui_edge``).

Platform faces lead, in the order a machine is likely to have one: Windows
(Leelawadee UI), macOS (Thonburi, Sukhumvit Set), then the Linux Noto and TLWG
packages. ``writing_system="Thai"`` turns on the S22 probe, and
``bundled_fallback`` names the face registered when the probe finds none -- a
bare Linux install has no Thai face at all and would otherwise draw boxes.
Noto Sans Thai is the bundled one despite mis-stacking some tone marks on macOS
(notofonts/thai #3): macOS always has Thonburi, so the bundled face is only ever
reached where nothing else exists.
"""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle

__all__ = ["TH_CONTENT_STYLE", "TH_FONT_FAMILIES", "th_zwsp_wrap"]

TH_FONT_FAMILIES: tuple[str, ...] = (
    "Leelawadee UI",
    "Thonburi",
    "Sukhumvit Set",
    "Noto Sans Thai",
    "Noto Serif Thai",
    "Garuda",
    "Waree",
    "Tahoma",
)


def th_zwsp_wrap(text: str) -> str:
    """Return *text* unchanged. Task 11 replaces this with the real transform."""
    return text


TH_CONTENT_STYLE = ContentTextStyle(
    font_role="th",
    families=TH_FONT_FAMILIES,
    wrap=th_zwsp_wrap,
    writing_system="Thai",
    bundled_fallback="NotoSansThai-Regular.ttf",
)
