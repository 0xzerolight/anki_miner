"""Spanish POS gate, sentence abbreviations, known-word leading words and the verb-repair tables.

``ES_EXCLUDED_SUBTYPES`` is empty on evidence: ``es_core_news_sm`` has no
``tagger`` component, so ``tag_`` equals ``pos_`` on every token (all 50,098
tokens of the OpenSubtitles 50k list and the whole corpus) and ``pos2`` is always
empty — a fine-tag table would be dead config (``test_es_pos_corpus.py`` pins it).

``ES_ABBREVIATIONS`` is spaCy's Spanish tokenizer exceptions ending in ``.``
(multi-token ``EE. UU.`` entering by both tokens, the ``1a.m.``/``1p.m.``/``12m.``
time forms by their letters) plus ``p``, so that the RAE's spaced ``p. ej.``,
``a. m.`` and ``p. m.`` do not split. The bare letters ``p`` and ``m`` are the only
single-letter keys; none of the keys is an ordinary Spanish word.

``ES_GENDER_LABELS`` puts the article in the gender field (A.3: never glued to
the expression). It is the noun's gender, so ``agua`` (feminine, written with
``el`` before a stressed ``a``) shows ``la``.

The enclitic slot table drives ``tokenizer.SpanishVerbRepair`` (es plan D1), not
a lookup rung: once the repair fronts the infinitive, the dictionary answers on
the front or on the surface's own form-of row, and a stripped-stem rung over the
surface would mostly find the wrong word (``nate`` → ``na``; es plan D2).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED

#: The model package the tokenizer loads and the availability probe looks for.
ES_MODEL_PACKAGE = "es_core_news_sm"

ES_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
ES_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

ES_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles and forms of address
        "sr", "sra", "srta", "dr", "dra", "prof", "profa", "ing", "lic", "gral", "ud", "uds", "vd", "vds",
        # places, companies, government
        "av", "avda", "apdo", "dpto", "esq", "cía", "s.a", "s.l", "s.r.l", "gob", "ee", "uu", "ee.uu",
        # references and Latin
        "p", "ej", "p.ej", "etc", "fig", "pág", "núm", "vol", "aprox", "dna", "p.d", "m.n", "s.s.s", "q.e.p.d",
        # dates and times
        "a.c", "d.c", "a.j.c", "d.j.c", "j.c", "a.m", "p.m", "m",
    }
)  # fmt: skip

#: Leading words a deck front carries that the mined lemma never does (S3): ``el perro`` meets ``perro``.
ES_LEADING_WORDS: frozenset[str] = frozenset({"el", "la", "los", "las", "un", "una", "unos", "unas"})

ES_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "el", "fem": "la"})

#: Enclitic pronouns by position in a chain, left to right: se · te/os · me/nos ·
#: lo/la/los/las/le/les (``díselo``, ``dámelo``, ``cómetelo``). A chain is at
#: most three long and strictly increasing, so no pronoun repeats.
ENCLITIC_SLOTS: Mapping[str, int] = MappingProxyType(
    {"se": 0, "te": 1, "os": 1, "me": 2, "nos": 2, "lo": 3, "la": 3, "los": 3, "las": 3, "le": 3, "les": 3}
)
_ENCLITIC_TAIL = re.compile(r"(nos|los|las|les|se|te|os|me|lo|la|le)$")
_MAX_ENCLITICS = 3
_MIN_STEM = 2

#: The closed class of unaccented one-syllable tú imperatives (``dime``, ``dame``,
#: ``hazlo``, ``ponlo``, ``tenlo``, ``vente``, ``salte``, ``vete``).
IRREGULAR_IMPERATIVES: Mapping[str, str] = MappingProxyType(
    {
        "di": "decir",
        "da": "dar",
        "haz": "hacer",
        "pon": "poner",
        "ten": "tener",
        "ven": "venir",
        "sal": "salir",
        "ve": "ir",
    }
)

_ACUTE = "\u0301"  # COMBINING ACUTE ACCENT


def strip_acute(text: str) -> str:
    """Drop the acute accent only: ``dámelo`` → ``damelo``; ``ñ`` and ``ü`` survive."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", text).replace(_ACUTE, ""))


def has_acute(text: str) -> bool:
    return _ACUTE in unicodedata.normalize("NFD", text)


def enclitic_splits(word: str) -> list[tuple[str, tuple[str, ...]]]:
    """Every ``(stem, chain)`` a valid enclitic chain leaves on *word*, shallowest first."""
    out: list[tuple[str, tuple[str, ...]]] = []
    stem: str = word
    chain: tuple[str, ...] = ()
    while len(chain) < _MAX_ENCLITICS:
        match = _ENCLITIC_TAIL.search(stem)
        if match is None or match.start() < _MIN_STEM:
            break
        clitic = match.group(1)
        if chain and ENCLITIC_SLOTS[clitic] >= ENCLITIC_SLOTS[chain[0]]:
            break
        stem, chain = stem[: match.start()], (clitic, *chain)
        out.append((stem, chain))
    return out
