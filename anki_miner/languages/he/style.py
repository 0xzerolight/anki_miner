"""Content typography for mined Hebrew text (data only, like zh/style.py and fa/style.py).

Hebrew runs right to left, so the content widgets and the card's word and sentence fields carry the
direction (S21), and the family list is probed against the faces installed FOR THE HEBREW WRITING
SYSTEM (S22) -- a machine with a dozen Latin faces and no Hebrew one would otherwise pick one that
draws every letter as a box.

Noto Sans Hebrew is the last resort, registered only when that probe finds nothing. It is the
reference modern Hebrew face, it covers the niqqud this language stores verbatim in its sentences,
and its Latin coverage keeps a mixed line readable. The order below puts one real family per
desktop ahead of it, so the bundled file is never loaded on a machine that has its own.

Niqqud is small: the curator's font-size helper text notes that pointed text needs 18 px and above
to stay legible.
"""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle

#: Ordered candidates: the bundled face, then the Windows, macOS and Linux Hebrew
#: faces, then the DejaVu family a bare Linux install still has.
HE_FONT_FAMILIES: tuple[str, ...] = (
    "Noto Sans Hebrew",
    "Segoe UI",
    "Tahoma",
    "Arial Hebrew",
    "SF Hebrew",
    "David CLM",
    "Frank Ruehl CLM",
    "DejaVu Sans",
)


def he_wrap(text: str) -> str:
    """Identity: Hebrew is space-delimited, so it already has break opportunities."""
    return text


HE_CONTENT_STYLE = ContentTextStyle(
    font_role="he",
    families=HE_FONT_FAMILIES,
    wrap=he_wrap,
    direction="rtl",
    writing_system="Hebrew",
    bundled_fallback="NotoSansHebrew-Regular.ttf",
)
