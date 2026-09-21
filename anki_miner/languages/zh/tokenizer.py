"""jieba-backed tokenizer producing fugashi-shaped duck tokens.

Emits ``LanguageToken`` (NOT ``morphology.SyntheticToken``): that class's
isinstance gates drive ja-only attested-reading and span-replacement merge
passes, and a zh token caught by them would be swept into Japanese morphology.

Surfaces are slices of the text as given, never re-spelled or normalised.
``morphology.iter_token_spans`` (:380) locates each token by ``str.find`` from a
running cursor and silently drops what it cannot find, so a normalised surface
would delete the word from the mined set with no error anywhere.

jieba's dictionary is simplified-only: on traditional text it splits 然後 into 然
and 後 (tagged as a name). The cut therefore runs on a simplified copy, and each
segment's length is sliced back out of the original text. OpenCC's simplified
conversion keeps the character count; a copy whose length differs, or a cut that
does not cover the text, falls back to cutting the original.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.overrides import ZH_FLAG_OVERRIDES, ZH_SPLIT_ENTRIES
from anki_miner.languages.zh.variants import to_simplified
from anki_miner.services.tagger import LockedTagger


class JiebaTagger:
    """Callable with the fugashi ``Tagger`` surface the parser already consumes."""

    def __init__(self, cutter: Any) -> None:
        self._cutter = cutter

    def _segments(self, text: str) -> list[tuple[str, str]]:
        """``(original-text slice, flag)`` per jieba segment of ``text``.

        ``ZH_FLAG_OVERRIDES`` is applied here, after the cut, so a retag can
        never move a token boundary; it is keyed on the simplified segment so
        traditional text hits the same row on the length-preserving branch. The
        fallback below cuts the ORIGINAL text, so its segments carry the source
        spelling and a traditional word misses its retag there. posseg's own
        ``word_tag_tab`` cannot do this job: a run of single characters goes to
        the HMM, whose tags come from ``char_state_tab``, and ``initialize()``
        rebuilds the tab anyway.
        """
        simplified = to_simplified(text)
        if len(simplified) == len(text):
            segments: list[tuple[str, str]] = []
            pos = 0
            for pair in self._cutter.cut(simplified):
                end = pos + len(pair.word)
                segments.append((text[pos:end], ZH_FLAG_OVERRIDES.get(pair.word, pair.flag)))
                pos = end
            if pos == len(text):
                return segments
        return [(pair.word, ZH_FLAG_OVERRIDES.get(pair.word, pair.flag)) for pair in self._cutter.cut(text)]

    def __call__(self, text: str) -> list[LanguageToken]:
        tokens: list[LanguageToken] = []
        for surface, flag in self._segments(text):
            if not surface:
                continue
            flag = flag or "x"
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

    ``del_word`` zeroes the row in THIS tokenizer's own ``FREQ`` (it also forces
    the lazy ``initialize()``, moving that cost off the first cut). Its one
    process-wide effect is ``finalseg.Force_Split_Words``, which only plain
    ``jieba.cut`` consults — nothing here imports plain jieba.
    """
    import jieba.posseg

    cutter = jieba.posseg.POSTokenizer()
    for word in ZH_SPLIT_ENTRIES:
        cutter.del_word(word)
    return LockedTagger(JiebaTagger(cutter))
