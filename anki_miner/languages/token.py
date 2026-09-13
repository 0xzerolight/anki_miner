"""fugashi-shaped duck token for non-ja tokenizers."""

from __future__ import annotations

from types import SimpleNamespace

__all__ = ["LanguageToken"]


class LanguageToken:
    """fugashi-shaped duck token for non-ja tokenizers.

    Deliberately NOT a SyntheticToken subclass: morphology.py's isinstance
    gates are ja-only merge passes that must never see these.

    ``morph`` carries the engine's morphological features verbatim (spaCy's
    ``str(tok.morph)``, e.g. ``Gender=Masc|Number=Sing``) for render hooks. It is
    a token attribute rather than a ``feature`` field because fugashi nodes have
    no such attribute and the parser reads it with a ``getattr`` default.
    """

    __slots__ = ("surface", "feature", "morph")

    def __init__(
        self, surface: str, pos1: str, pos2: str = "", lemma: str = "", kana: str = "", morph: str = ""
    ) -> None:
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana)
        self.morph = morph
