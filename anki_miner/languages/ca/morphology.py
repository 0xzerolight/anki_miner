"""Catalan data for the shared spaCy substrate (spec Appendix E, ca column).

``CA_EXCLUDED_SUBTYPES`` is empty on evidence: ``ca_core_news_sm`` has no
trained tagger, so ``tag_`` copies ``pos_`` on every token (0 of 59,483 UD
Catalan AnCora test tokens differ) and ``pos2`` is always ``""`` — dead config,
as for ko (E.2.1, D11).

``CA_ABBREVIATIONS`` is seeded from the dotted entries of spaCy's Catalan
tokenizer exceptions (``spacy/lang/ca/tokenizer_exceptions.py``), casefolded
with the final dot dropped, plus ``núm`` (listed there without its dot, written
``núm.``). ``set`` (setembre) is left out: ``Tinc set.`` and ``Són les set.``
end sentences. The shared single-letter exceptions are not taken either.

The interpunct: ``col·legi`` is written with U+00B7 and every wty-ca-en key
uses it (2,836 ``l·l`` keys; none spelled ``l.l``, ``l-l`` or with U+0140). In
the wild it degrades to ``col.legi``, ``col-legi`` or ``colegi``, so
``interpunct_variants`` maps those back as lookup-only rungs (R35, D6); the
opposite direction never meets a key.

``CA_LEADING_WORDS`` feeds the shared ``spaced_dedup_fold``, which also strips
the glued elided article (``l'home`` meets the mined ``home``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED

#: The model package the tokenizer loads and the availability probe looks for.
CA_MODEL_PACKAGE = "ca_core_news_sm"

CA_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
CA_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

CA_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles
        "dr", "dra", "sr", "sra", "srta", "st", "sta",
        # months (set, setembre, deliberately absent)
        "gen", "feb", "abr", "jul", "oct", "nov", "dec",
        # other
        "aprox", "pàg", "p.ex", "pl", "núm",
        # accented-letter ordinals
        "à", "è", "é", "í", "ò", "ó", "ú",
    }
)  # fmt: skip

#: Leading words a deck front carries that the mined lemma never does (S3, E.1 D18).
CA_LEADING_WORDS: frozenset[str] = frozenset({"el", "la", "l'", "l’", "els", "les", "un", "una", "en", "na"})

#: noun_gender prints the article as its label (E.10 D1); Catalan has no neuter nouns.
CA_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "el", "fem": "la"})

_L_DOT_LETTERS = {0x0140: "l·", 0x013F: "L·"}
_DEGRADED_INTERPUNCT_RE = re.compile(r"(?<=l)[.\-](?=l)", re.IGNORECASE)
_INTERPUNCT = "·"


def ca_normalize(text: str) -> str:
    """``LanguageProfile.normalize``: NFC, then the legacy ``ŀ``/``Ŀ`` letters as ``l·``/``L·``.

    Never NFKC: U+00B7 must survive (R35). Apostrophes stay verbatim; the
    tagger folds curly ones in its own copy.
    """
    return unicodedata.normalize("NFC", text).translate(_L_DOT_LETTERS)


def _restored_interpunct(text: str) -> list[str]:
    """The standard ``l·l`` spellings of one degraded text (``col.legi``, ``col-legi``, ``colegi``)."""
    if not text or _INTERPUNCT in text:
        return []
    restored = _DEGRADED_INTERPUNCT_RE.sub(_INTERPUNCT, text)
    if restored != text:
        return [restored]
    variants: list[str] = []
    for i in range(1, len(text) - 1):
        char, before, after = text[i], text[i - 1], text[i + 1]
        if char in "lL" and before.isalpha() and after.isalpha() and before not in "lL" and after not in "lL":
            variants.append(f"{text[: i + 1]}{_INTERPUNCT}{char}{text[i + 1 :]}")
    return variants


def interpunct_variants(word: str, surface: str) -> list[str]:
    """``LatinLookupStrategy`` rung over ``(mined_form, surface)``: restored ``l·l`` spellings.

    The mined form's (the lemma's) variants lead: a lemma ``colegi`` from the
    surface ``colegis`` reaches the full ``col·legi`` entry before the
    ``col·legis`` form-of stub, which renders only its target word.
    """
    variants: list[str] = []
    for text in (word, surface):
        for variant in _restored_interpunct(text):
            if variant not in variants:
                variants.append(variant)
    return variants
