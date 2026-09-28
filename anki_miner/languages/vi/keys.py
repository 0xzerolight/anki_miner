"""Vietnamese index keys, comparison fold and wty row rank (spec C.4, R6/R7 in the R33 shape).

One function at both seams, the ar/fa/th/vi/id/he convention: ``fold_term`` builds
dictionary and frequency keys on import and query, and the profile's ``dedup_fold`` is the
same function (Vietnamese has no article to strip). NFC -> word-scoped old-style tone
placement (``fold_tone_placement``, never the raw port: a key fold that moved the tilde of
a Spanish name would put that spelling on the card front, because the front is
``vi_fold_term(surface)``) -> casefold; the tone move and casefold commute because every
precomposed Vietnamese vowel lowercases to its own precomposed lowercase. Rule A then
Rule A' homograph scope comes with :class:`CasefoldDictKeys`; the lemma is the folded
surface, so A' adds nothing. ``fold_reading`` stays NFC: wty-vi-en carries no readings.

The row rank (:meth:`VietnameseDictKeys.sense_rank`) is the shared wty rank read through
the tokenizer's VLSP tags instead of UPOS.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.keys import NAME_ROW_TAGS, CasefoldDictKeys
from anki_miner.languages.vi.script import fold_tone_placement

#: A token's VLSP pos1 (vi/pos.py) -> the first tags of the wty-vi-en rows stating that word
#: class, which then lead the definition. wty-vi-en has one row per etymology and part of
#: speech, in import order, so without this là opened on "fine silk" (n) before the copula (v),
#: bị on "big sack", sống on "spine" and bớt on "birthmark". wty-vi-en writes its classifier
#: rows with no tag at all, so a classifier (Nc) leads with its empty-tag row: người "indicates
#: people", cuốn "indicates books", where ``n`` put cái on "utensil". On 76 ordinary lines 20
#: of 178 cards change their lead row: 14 better, 3 worse (tagger slips: nhau tagged N opens
#: on "placenta"; hơn and đủ tagged A lose "more" and "enough"). A stays in, unlike the shared
#: rank's ADJ: of 53 A cards changed over 3,626 wty example lines about 30 gain (tự do "free"
#: over "freedom", sướng "happy" over "rice field") and 5 lose.
VI_ROW_TAGS_BY_POS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "V": frozenset({"v"}),
        "N": frozenset({"n"}),
        "Nu": frozenset({"n"}),
        "Nc": frozenset({""}),
        "A": frozenset({"adj"}),
    }
)

#: The VLSP proper-noun tag: only such a token keeps its proper-name rows in place.
_PROPER_NOUN = "Np"

#: wty-vi-en files a letter two ways: a ``char`` row ("The 18th letter of the Vietnamese
#: alphabet") and an ``n`` row whose first gloss is Wiktionary's letter-name template ("The name
#: of the Latin script letter Ô/ô.", 54 rows, digraphs included). Both led ô three rows before
#: "umbrella"; the second also sits among the nouns of bờ, cờ, dê, đê, tê and u. The CJK
#: ``char`` rows (chữ Hán/Nôm) never meet a token: the parser's script gate
#: (``is_vietnamese_word``) passes Vietnamese syllables only.
_LETTER_ROW_TAG = "char"
_LETTER_NAME_GLOSS = "The name of the Latin"


class VietnameseDictKeys(CasefoldDictKeys):
    """:class:`CasefoldDictKeys` whose row rank reads VLSP tags and puts letter rows last."""

    def sense_rank(self, content: str, tags: str, pos: str | None) -> int:
        """``0`` for a row of the token's own word class (``VI_ROW_TAGS_BY_POS``); ``2`` for a
        letter row, and for a proper-name row unless the token is itself ``Np``; ``1`` otherwise.

        Same levels as the shared rank, so storage keeps the index order inside each one and
        drops nothing.
        """
        first = tags.split(" ", 1)[0]
        if first == _LETTER_ROW_TAG or _LETTER_NAME_GLOSS in content:
            return 2
        if first in NAME_ROW_TAGS and pos != _PROPER_NOUN:
            return 2
        return 0 if first in VI_ROW_TAGS_BY_POS.get(pos or "", frozenset()) else 1


VI_KEYS = VietnameseDictKeys(extra_fold=fold_tone_placement)


def vi_fold_term(text: str) -> str:
    """``VI_KEYS.fold_term``: the key fold and the known-words comparison fold (idempotent)."""
    return VI_KEYS.fold_term(text)
