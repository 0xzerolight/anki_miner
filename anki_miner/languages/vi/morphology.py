"""Vietnamese lookup-miss ladder (spec C.4).

The card front is the folded surface (tokenizer.py: NFC -> old style ->
casefold), probed first by the definition chain. On a miss the candidates are,
in order, all with conditions 0: the y-to-i monophthong spelling, a
reduplicative's base syllable, and the Icelandic-eth repair for text that
reached the ladder without the P3 normaliser. No new-style tone rung (plan
decision 8): every vi index key and every query is folded to old style by
``VI_KEYS``, so it could only re-probe the key that just missed.
"""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages.vi.script import ETH_REPAIR

_TONE_MARKS = frozenset(map(chr, (0x0300, 0x0301, 0x0303, 0x0309, 0x0323)))
_Y_TO_I = str.maketrans("yýỳỷỹỵYÝỲỶỸỴ", "iíìỉĩịIÍÌỈĨỊ")
#: Onsets before which a lone ``y`` is the ``i`` monophthong (spec C.4: never after ``qu``).
_Y_ONSET = re.compile(r"ngh|ng|nh|ch|gh|kh|ph|th|tr|[bcdđghklmnprstvx]")
_ONSET = re.compile(r"ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdđghklmnprstvx]")


def _toneless(syllable: str) -> str:
    decomposed = unicodedata.normalize("NFD", syllable)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _TONE_MARKS)).lower()


def y_to_i(word: str) -> str:
    """Each syllable onset + ``y`` (+ tone) gets ``i``; ``""`` when no syllable qualifies."""
    syllables = word.split(" ")
    changed = False
    for index, syllable in enumerate(syllables):
        base = _toneless(syllable)
        if len(base) >= 2 and base[-1] == "y" and _Y_ONSET.fullmatch(base[:-1]):
            syllables[index] = syllable[:-1] + syllable[-1].translate(_Y_TO_I)
            changed = True
    return " ".join(syllables) if changed else ""


def reduplicative_base(word: str) -> str:
    """The first syllable of a two-syllable word whose syllables share an onset."""
    syllables = word.split(" ")
    if len(syllables) != 2:
        return ""
    if syllables[0] == syllables[1]:
        return syllables[0]
    first, second = (_ONSET.match(_toneless(syllable)) for syllable in syllables)
    if first is not None and second is not None and first.group(0) == second.group(0):
        return syllables[0]
    return ""


class VietnameseLookup:
    """LookupStrategy: y-to-i, reduplicative base, eth repair; conditions 0, the probe never repeated."""

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        del orth_base, ctype  # the front is already the folded surface; duck tokens carry no cType
        out: list[str] = []
        for text in (y_to_i(word), reduplicative_base(word), word.translate(ETH_REPAIR)):
            if text and text != word and text not in out:
                out.append(text)
        return [(text, 0) for text in out]


__all__ = ["VietnameseLookup", "reduplicative_base", "y_to_i"]
