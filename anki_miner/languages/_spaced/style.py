"""Content typography for mined Latin-script text (data only, like zh/style.py)."""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle

#: Faces with full Latin Extended coverage, Windows/macOS/Linux; Qt takes the first installed.
SPACED_FONT_FAMILIES: tuple[str, ...] = ("Noto Sans", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans")


def spaced_wrap(text: str) -> str:
    """Identity: space-delimited text already has break opportunities."""
    return text


SPACED_CONTENT_STYLE = ContentTextStyle(font_role="latin", families=SPACED_FONT_FAMILIES, wrap=spaced_wrap)
