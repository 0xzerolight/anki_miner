"""fugashi-shaped duck token for non-ja tokenizers."""

from __future__ import annotations

from types import SimpleNamespace

__all__ = ["LanguageToken"]


class LanguageToken:
    """fugashi-shaped duck token for non-ja tokenizers.

    Deliberately NOT a SyntheticToken subclass: morphology.py's isinstance
    gates are ja-only merge passes that must never see these.
    """

    __slots__ = ("surface", "feature")

    def __init__(self, surface: str, pos1: str, pos2: str = "", lemma: str = "", kana: str = "") -> None:
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana)
