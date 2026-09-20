"""Hebrew duck tokens: one regex, a function-word tier, and the spans the card stores.

Runs over the NORMALISED line -- ``script.he_normalize`` has already run through the profile's
``normalize`` -- so every surface here is a verbatim slice of the line the card will store, niqqud
and all. The form resolver (``morphology.HebrewLemmaPass``) rewrites ``lemma`` and ``pos1``
afterwards; with no dictionary wired it does not run and the folded surface is the front.

**The word class is letters OR Hebrew combining marks.** Hebrew points are category ``Mn``, which
Python's ``\\w`` does not match, so a class of ``[^\\W\\d_]`` alone shatters a vocalised line into
one token per character -- measured: ``ha-yeled halakh la-gan`` came out as 21 tokens instead of 4.
The marks come from ``script.HE_MARK_CLASS``, which is the 51 ``Mn`` members only: U+05BE MAQAF is
a separator and U+05C0/05C3/05C6 are punctuation, and a sof pasuq glued onto the preceding word
would print on the card front.
"""

from __future__ import annotations

import re

from anki_miner.languages.he.script import GERESH, GERSHAYIM, HE_MARK_CLASS, MAQAF, he_fold, is_he_mark
from anki_miner.languages.he.stopwords import HE_FUNCTION_WORDS
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

__all__ = ["TOKEN_RE", "HebrewTagger", "build_tagger", "to_duck_tokens"]

#: A word character: any letter, or a Hebrew combining mark.
_LETTER = "(?:[^\\W\\d_]|[" + HE_MARK_CLASS + "])"
#: What may join two word runs inside ONE token: the two quote marks a subtitle types, the two
#: Hebrew ones wty keys, the ASCII hyphen and the maqaf.
_JOINER = "[\"'" + GERSHAYIM + GERESH + "\\-" + MAQAF + "]"
#: A trailing geresh or apostrophe marks an abbreviation and belongs to the token.
_TRAILER = "[" + GERESH + "']?"

TOKEN_RE = re.compile(_LETTER + "+(?:" + _JOINER + _LETTER + "+)*" + _TRAILER + "|\\d+(?:[.,:]\\d+)*|[^\\s\\w]")

_ASCII_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_NUMBER_RE = re.compile(r"\d+(?:[.,:]\d+)*\Z")


def _pos1(surface: str) -> str:
    """WORD, NUM, PUNCT or LATIN -- everything the tokenizer alone can tell."""
    if _NUMBER_RE.match(surface):
        return "NUM"
    if any(char in _ASCII_LETTERS for char in surface):
        return "LATIN"
    if not any(char.isalnum() or is_he_mark(char) for char in surface):
        return "PUNCT"
    return "WORD"


def _pos2(surface: str, folded: str) -> str:
    """``stopword`` for the function-word tier, ``abbrev`` for a gershayim or geresh form."""
    if folded in HE_FUNCTION_WORDS:
        return "stopword"
    inner = surface[1:-1]
    if GERSHAYIM in inner or '"' in inner or surface.endswith((GERESH, "'")):
        return "abbrev"
    return ""


def to_duck_tokens(line: str) -> list[LanguageToken]:
    """Tokenise one normalised Hebrew line. Surfaces are verbatim slices."""
    tokens: list[LanguageToken] = []
    for match in TOKEN_RE.finditer(line):
        surface = match.group()
        folded = he_fold(surface)
        pos1 = _pos1(surface)
        # LanguageToken takes pos1 POSITIONALLY; a keyword-only call is a TypeError.
        tokens.append(LanguageToken(surface, pos1, _pos2(surface, folded) if pos1 == "WORD" else "", folded, ""))
    return tokens


class HebrewTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> tokens``."""

    def __call__(self, text: str, **_: object) -> list[LanguageToken]:
        return to_duck_tokens(text)

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """Build the lock-guarded Hebrew tokenizer (``tagger_provider``'s entry point).

    No engine, no table, no pack: Hebrew's whole tokenizer is the regex above, so this never
    raises ``ImportError`` and a Hebrew install is always able to mine.
    """
    return LockedTagger(HebrewTagger())
