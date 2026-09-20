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

import logging

from anki_miner.languages.profile import ContentTextStyle

logger = logging.getLogger(__name__)

ZWSP = "\N{ZERO WIDTH SPACE}"

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


def _tagger():
    """The shared Thai tokenizer, through the provider that already caches one."""
    from anki_miner.languages.tagger_provider import get_tagger

    return get_tagger("th")


def th_zwsp_wrap(text: str) -> str:
    """Insert U+200B between newmm tokens so Qt can break a Thai line.

    DISPLAY ONLY. Callers keep the pristine string on every other surface --
    COPY_ROLE, tooltips and above all model data -- exactly as
    ``gui/utils/phrase_wrap.py`` requires: stripping the inserted character from
    the output always yields the input unchanged. The U+202A-in-card-text dedup
    bug is the cautionary tale.

    Joining with U+200B rather than the ja wrapper's U+2060 WORD JOINER is the
    opposite operation for the opposite problem: ja has too MANY break
    opportunities and glues the inside of each phrase, Thai has none at all and
    needs one added at each token boundary.

    Never raises: a machine without the engine gets its text back unwrapped,
    which is today's behaviour, not a crash in a paint path.
    """
    if not text:
        return text
    try:
        tokens = _tagger().parse(text)
    except Exception:  # noqa: BLE001 - a display path must never take the widget down
        logger.debug("Thai wrap unavailable; returning the text unwrapped", exc_info=True)
        return text
    out: list[str] = []
    cursor = 0
    for token in tokens:
        found = text.find(token.surface, cursor)
        if found < 0:
            return text
        out.append(text[cursor:found])
        if out and found > 0 and not text[found - 1].isspace() and text[found - 1] != ZWSP:
            out.append(ZWSP)
        out.append(token.surface)
        cursor = found + len(token.surface)
    out.append(text[cursor:])
    return "".join(out)


TH_CONTENT_STYLE = ContentTextStyle(
    font_role="th",
    families=TH_FONT_FAMILIES,
    wrap=th_zwsp_wrap,
    writing_system="Thai",
    bundled_fallback="NotoSansThai-Regular.ttf",
)
