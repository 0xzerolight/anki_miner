"""jieba-backed tokenizer producing fugashi-shaped duck tokens.

Emits ``LanguageToken`` (NOT ``morphology.SyntheticToken``): that class's
isinstance gates drive ja-only attested-reading and span-replacement merge
passes, and a zh token caught by them would be swept into Japanese morphology.

Surfaces are the raw jieba segments, never re-spelled or normalised.
``morphology.iter_token_spans`` (:380) locates each token by ``str.find`` from a
running cursor and silently drops what it cannot find, so a normalised surface
would delete the word from the mined set with no error anywhere.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger


class JiebaTagger:
    """Callable with the fugashi ``Tagger`` surface the parser already consumes."""

    def __init__(self, cutter: Any) -> None:
        self._cutter = cutter

    def __call__(self, text: str) -> list[LanguageToken]:
        tokens: list[LanguageToken] = []
        for pair in self._cutter.cut(text):
            surface = pair.word
            if not surface:
                continue
            flag = pair.flag or "x"
            tokens.append(
                LanguageToken(
                    surface=surface,
                    # pos1 is the coarse class (the flag's first letter), pos2
                    # the full flag when it carries more (nz, vn, ns); "" when
                    # the flag is already one letter, matching unidic's blank
                    # sub-POS rather than repeating the value.
                    pos1=flag[0],
                    pos2="" if len(flag) == 1 else flag,
                    # No inflection in Chinese: the dictionary form IS the surface.
                    lemma=surface,
                    kana="",
                )
            )
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """Build a lock-guarded jieba tokenizer.

    A private ``POSTokenizer`` rather than the ``jieba.posseg`` module-level
    default: the shared default is mutated by any other jieba user in the
    process. ``LockedTagger`` is reused verbatim from the ja stack — jieba
    builds its prefix dictionary lazily on first cut and documents no thread
    safety, which is the same hazard the ja lock already covers.
    """
    import jieba.posseg

    return LockedTagger(JiebaTagger(jieba.posseg.POSTokenizer()))
