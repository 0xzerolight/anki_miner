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
from typing import TYPE_CHECKING, Any

from anki_miner.languages.vi.pos import NAME_TAG
from anki_miner.languages.vi.script import ETH_REPAIR

if TYPE_CHECKING:
    from anki_miner.services.morphology import AttestLookup, FormLookup

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


#: A token right after one of these starts a new sentence: its capital says nothing about a name.
_SENTENCE_BREAKS = frozenset('.!?…:-–—"“”«»')


def _title_case(surface: str) -> bool:
    """Every syllable capitalised and otherwise lower case (a name, not an acronym, not a compound)."""
    return any(char.isalpha() for char in surface) and all(
        syllable[:1].isupper() and not any(char.isupper() for char in syllable[1:]) for syllable in surface.split(" ")
    )


def _starts_sentence(tokens: list[Any], index: int) -> bool:
    if index == 0:
        return True
    previous = tokens[index - 1]
    return previous.feature.pos1 == "CH" and previous.surface[-1:] in _SENTENCE_BREAKS


class VietnameseNamePass:
    """token_post_pass (spec C.4 name tier): mid-sentence, title-cased, unattested -> pos2 ``name``.

    underthesea tags most names ``Np`` (never mineable); this catches the ones it files
    as ``N``/``V``/``A``. A token already carrying a tier (``stopword``) or tagged ``Np``
    is left alone, as is a sentence start. One attestation probe per line over the
    distinct suspects; ``attest is None`` (no offline dictionary) makes the pass inert,
    because without a dictionary a capital cannot be told from a name. The third
    argument (R36's form lookup) is ignored.
    """

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del forms
        if attest is None:
            return tokens
        suspects = [
            token
            for index, token in enumerate(tokens)
            if token.feature.pos1 != "Np"
            and not token.feature.pos2
            and _title_case(token.surface)
            and not _starts_sentence(tokens, index)
        ]
        if not suspects:
            return tokens
        attested = attest(list(dict.fromkeys(token.surface for token in suspects)))
        for token in suspects:
            if token.surface not in attested:
                token.feature.pos2 = NAME_TAG
        return tokens


__all__ = ["VietnameseLookup", "VietnameseNamePass", "reduplicative_base", "y_to_i"]
