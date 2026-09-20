"""Hebrew POS defaults and the dictionary-tag mapping (spec F.2).

Hebrew has no tagger, so ``pos1`` is what the tokenizer can see (``WORD``, ``NUM``, ``PUNCT``,
``LATIN``) until the form resolver upgrades it from the resolved row's Wiktionary tags. That upgrade
is the one deliberate delta from the id shape, and it is what makes ``PROPN`` excludable at all:
1,572 lemma rows carry a ``name*`` tag.

``allowed_pos`` is R33 verbatim, the five values included. ``abbrev`` is a declared subtype the POS
editor offers and the default does NOT exclude: ladder rung (1) exists precisely to resolve
abbreviations, and excluding them by default would make that rung dead code.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["HE_ALLOWED_POS", "HE_EXCLUDED_SUBTYPES", "HE_POS_LABELS", "HE_TAG_TO_POS", "pos_from_tags"]

#: Mined by default. ``WORD`` is the unresolved token, which must stay mineable: with no tagger it
#: is the honest answer for anything the dictionary does not know.
HE_ALLOWED_POS: tuple[str, ...] = ("WORD", "NOUN", "VERB", "ADJ", "ADV")
HE_EXCLUDED_SUBTYPES: tuple[str, ...] = ("stopword",)

HE_POS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "WORD": "Word (unresolved)",
        "NOUN": "Noun",
        "VERB": "Verb",
        "ADJ": "Adjective",
        "ADV": "Adverb",
        "PROPN": "Proper noun",
        "FUNC": "Function word",
        "NUM": "Number",
        "PUNCT": "Punctuation",
        "LATIN": "Latin/other",
        "stopword": "Stopword",
        "abbrev": "Abbreviation",
    }
)

#: The FIRST token of a wty-he-en definition-tag string, mapped to a pos1. Measured over the
#: 15,308 lemma rows of revision 2026.09.19: n 7,873, v 2,912, name 1,572, adj 1,434, adv 388,
#: r 338, prep 172, phrase 143, intj 134, num 49, pron 47, char 41, conj 38, suf 33, pref 29,
#: ptcl 28, prep-phrase 23, det 21, prov 19, artic 2, pl 1, postp 1. ``r`` is Wiktionary's Semitic
#: ROOT part of speech -- a root is not a word a learner mines, so it lands with the function words.
HE_TAG_TO_POS: Mapping[str, str] = MappingProxyType(
    {
        "n": "NOUN",
        "v": "VERB",
        "adj": "ADJ",
        "adv": "ADV",
        "name": "PROPN",
        "prop-n": "PROPN",
        "prep": "FUNC",
        "prep-phrase": "FUNC",
        "postp": "FUNC",
        "conj": "FUNC",
        "pron": "FUNC",
        "det": "FUNC",
        "artic": "FUNC",
        "ptcl": "FUNC",
        "part": "FUNC",
        "pref": "FUNC",
        "suf": "FUNC",
        "num": "FUNC",
        "phrase": "FUNC",
        "prov": "FUNC",
        "intj": "FUNC",
        "char": "FUNC",
        "r": "FUNC",
    }
)


def pos_from_tags(tags: str) -> str:
    """The pos1 a definition-tag string implies; ``WORD`` for anything unmapped or absent.

    Only the first token is read: the rest are gender, number and register chips
    (``n masc``, ``v pl pref``, ``adj sl``), which the card fields read separately.
    """
    first = tags.split(" ")[0] if tags else ""
    return HE_TAG_TO_POS.get(first, "WORD")
