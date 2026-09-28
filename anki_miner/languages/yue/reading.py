"""Jyutping readings for the yue card reading field (spec F.1).

Readings go in their own field, never as ruby: jyutping is a full romanisation,
not a phonetic gloss of individual characters.

The word is handed to ``characters_to_jyutping`` as a ONE-ELEMENT LIST so the
engine cannot re-segment it, and the second element of its answer is ``None``
(not ``""``) for an out-of-vocabulary item, which is why every caller goes
through :func:`word_jyutping`.

The engine reads many everyday words with a literary or rare reading (聽 ting3,
返 faan2, 行 hong6), so ``data/jyutping_overrides.txt`` is consulted first: the
reading HKCanCor's Hong Kong speakers use for that word, built and hand-reviewed
by ``scripts/build_yue_jyutping_overrides.py``. The reading is also the
definition-ranking boost: a dictionary row keyed with the spoken reading leads.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def _spoken_readings() -> dict[str, str]:
    """``word -> jyutping`` from the committed override table, read once.

    Package data: it ships in the wheel and the bundle, not in the yue pack.
    """
    text = (files("anki_miner.languages.yue") / "data" / "jyutping_overrides.txt").read_text(encoding="utf-8")
    rows = (line.split("\t") for line in text.splitlines() if line and not line.startswith("#"))
    return {row[0]: row[1] for row in rows}


def word_jyutping(word: str) -> str:
    """Space-separated jyutping for ``word``; ``""`` when the engine has none."""
    if not word:
        return ""
    spoken = _spoken_readings().get(word)
    if spoken is not None:
        return spoken
    import pycantonese

    return pycantonese.characters_to_jyutping([word])[0][1] or ""


def jyutping_syllables(word: str) -> list[tuple[str, int]]:
    """``(syllable, tone)`` pairs -- the input the tone-colour render hook needs.

    The tone is the syllable's trailing digit; a syllable with none (which the
    engine does not emit for Han, but an imported reading may carry) reports 0
    and the hook falls back to the neutral colour.
    """
    pairs: list[tuple[str, int]] = []
    for syllable in word_jyutping(word).split():
        tone = int(syllable[-1]) if syllable[-1].isdigit() else 0
        pairs.append((syllable, tone))
    return pairs


class YueReadingSupport:
    """``ReadingSupport`` for yue: the token's LEMMA, read as one word.

    The lemma, not the surface: a surface joined across an interior space
    (``今 日``) is not a word the engine can look up.
    """

    def word_reading(self, token: Any) -> str:
        return word_jyutping(getattr(token.feature, "lemma", "") or token.surface)
