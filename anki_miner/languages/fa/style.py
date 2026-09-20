"""Content typography for mined Persian text (data only, like zh/style.py).

Persian runs right to left in the Arabic script, so the content widgets and the
card's word and sentence fields carry the direction (S21) and the family list is
probed against the faces installed FOR THAT SCRIPT (S22) -- a machine with a
dozen Latin faces and no Arabic one would otherwise pick one that draws every
letter as a box.

Vazirmatn is the last resort, registered only when that probe finds nothing: it
is a contemporary Persian face designed for the Iranian letterforms (a four-dot
yeh, a separated heh), and its Latin coverage means a mixed line stays readable
on a machine with nothing else. The order below is one real family per desktop
first, so the bundled file is never loaded on a machine that has its own.
"""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle

#: Ordered candidates: the bundled face, then Windows, macOS and Linux Arabic
#: faces, then the DejaVu family a bare Linux install still has.
FA_FONT_FAMILIES: tuple[str, ...] = (
    "Vazirmatn",
    "Segoe UI",
    "Tahoma",
    "SF Arabic",
    "Geeza Pro",
    "Noto Sans Arabic",
    "Noto Naskh Arabic",
    "DejaVu Sans",
)


def fa_wrap(text: str) -> str:
    """Identity: Persian is space-delimited, so it already has break opportunities."""
    return text


FA_CONTENT_STYLE = ContentTextStyle(
    font_role="fa",
    families=FA_FONT_FAMILIES,
    wrap=fa_wrap,
    direction="rtl",
    writing_system="Arabic",
    bundled_fallback="Vazirmatn-Regular.ttf",
)
