"""Finnish data for the shared spaCy substrate (spec E.1 fi column, E.2.3, E.10 D17/D18).

Evidence: real ``fi_core_news_sm`` 3.8.0 output over the 16,661 Finnish example sentences of wty-fi-en
2026.08.29 (statistics only) and the fixtures under ``tests/fixtures/fi/``.

``FI_EXCLUDED_SUBTYPES``: the model's ``tagger`` predicts TDT fine tags independently of the morphologizer's UPOS, so
a closed-class fine tag can sit under ADJ/ADV/NOUN/VERB. Only two are excluded, and only because every witness is a
half of a negation contraction rather than a word: ``Adv_V`` (``miksei`` tokenises as ``miks`` + ``ei``, both ADV) and
``C_V`` (``ell``, ``etteikö``). Everything else stays, including two tags an earlier draft excluded: ``Foreign``
carries real Finnish under an allowed UPOS (``hiilidioksidia``, ``liha-`` -> ``liha``, ``nr``) and ``Punct`` carries
``kymppiin`` -> ``kymppi``, while the English the two would have caught is mined anyway under the allowed tags
(``Hän lauloi one more time.`` mines ``one`` ADJ/``A`` and ``time`` NOUN/``N``) - so excluding them lost real words
with no trace and bought nothing. Ordinals (``Num``: ``ensimmäinen``) are vocabulary, and
``Pron``/``Adp``/``C``/``Interj``/``Symb`` also hang on real words (``kirjakin``, ``käsin``, ``kohta``, ``neiti``).

``FI_ABBREVIATIONS``: every dotted key of spaCy's Finnish tokenizer exceptions, final dot dropped, casefolded (65
keys, 64 entries: ``Mm.``/``mm.``). None is a Finnish word that ends a sentence. The same set is the tokenizer's
``abbreviations`` argument, so the sentence splitter and the tokenizer agree on what a dotted abbreviation is.

Clitics (``-kin -kaan -han -pa -ko -s``) and possessive suffixes (``-ni -si -nsa -mme -nne``) get no lookup rung.
The model drops them in context (``kirjakin``, ``kirjani`` mine as ``kirja``) and keeps them elsewhere (a
sentence-initial ``Kirjakin``, every ``Haluatko``). A string-strip rung would misfire (``tuttuin`` -> ``tuttu``), and
existence attestation cannot tell a stem from another inflected form (``haluatko`` -> ``haluat`` is a term too).

The card front is repaired instead, by the shared form-of pass (``_spaced/form_of.py``, wired in ``fi/parser.py``),
which reads what a wty-fi-en form row NAMES rather than whether a key exists: ``haluatko`` -> ``haluta``,
``lunta`` -> ``lumi``, ``elokuviin`` -> ``elokuva``. It leaves a front the dictionary files as a headword alone, so
a wrong headword stays (``tulit`` keeps the model's ``tuli``, fire). ``finnish_suffix_candidates`` adds the
spellings the model's own morph points at, tried after the surface: the surface without the ``Clitic=`` endings
(``Kirjoitatko`` -> ``kirjoitat``), and, under ``Person[psor]``, the stem before the possessive suffix plus the
genitive ``-n`` (``avaimeni`` -> ``avaimen``, ``veljensä`` -> ``veljen``), because the suffix replaces a nominative
or genitive ending whose bare stem is no word. Over 84 everyday subtitle lines the wrong fronts fall from 37 of 192
mined tokens to 13, and no front that was right turns wrong.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import nfc_normalize

#: The model package the tokenizer loads and the availability probe looks for.
FI_MODEL_PACKAGE = "fi_core_news_sm"

FI_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
FI_EXCLUDED_SUBTYPES: tuple[str, ...] = ("Adv_V", "C_V")

FI_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "aik", "alk", "alv", "ao", "ark", "as", "eaa", "ed", "em", "esim", "huom", "jne", "joht", "k", "ko", "ks",
        "lk", "lkm", "lyh", "läh", "miel", "milj", "ml", "mm", "myöh", "n", "nimim", "ns", "nyk", "oik", "os", "p",
        "par", "per", "pj", "po", "prof", "puh", "puh.joht", "pvm", "rak", "ry", "s", "siht", "so", "srk", "synt",
        "t", "tark", "til", "tms", "toim", "ts", "v", "vas", "vast", "vm", "vrt", "yht", "yl", "yliopp", "ym", "yms",
        "yo",
    }
)  # fmt: skip

_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None})


def fi_normalize(text: str) -> str:
    """S5 for Finnish: NFC; NBSP -> space; soft hyphens (e-book hyphenation points) removed; ä/ö/å untouched."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)


#: The endings each ``Clitic=`` value stands for, over every value ``fi_core_news_sm`` 3.8.0 predicts (``Han``,
#: ``Ka``, ``Kaan``, ``Kin``, ``Ko``, ``Pa``, ``S`` and the pairs ``Han,Ko``, ``Han,Pa``, ``Ko,S``, ``Pa,S``).
_CLITIC_ENDINGS: Mapping[str, str] = MappingProxyType(
    {"Han": "han|hän", "Ka": "ka|kä", "Kaan": "kaan|kään", "Kin": "kin", "Ko": "ko|kö", "Pa": "pa|pä", "S": "s"}
)
#: The possessive suffixes that replace a nominative or genitive singular ending (1sg, 2sg, 1pl, 2pl, 3).
_POSSESSIVE_RE = re.compile(r"(?:nsa|nsä|mme|nne|ni|si)$")
#: A stripped word keeps at least this many letters.
_MIN_STEM = 2


def _morph_values(morph: str, name: str) -> list[str]:
    """The values of one feature of a spaCy morph string (``Clitic=Han,Ko|Mood=Ind`` -> ``["Han", "Ko"]``)."""
    for feature in morph.split("|"):
        key, _, value = feature.partition("=")
        if key == name:
            return value.split(",")
    return []


def finnish_suffix_candidates(token: Any) -> list[str]:
    """``FormOfLemmaPass`` extra candidates: the lowered surface without the suffixes the model's morph marks.

    ``Clitic=`` strips those clitics (``puhutko`` -> ``puhut``, ``onkohan`` -> ``on``); ``Person[psor]`` then swaps
    the possessive suffix for the genitive ``-n`` (``avaimeni`` -> ``avaimen``). Morph without either gives none.
    """
    word = token.surface.lower()
    found: list[str] = []
    clitics = [_CLITIC_ENDINGS[value] for value in _morph_values(token.morph, "Clitic") if value in _CLITIC_ENDINGS]
    if clitics:
        bare = re.sub(f"(?:{'|'.join(clitics)}){{1,{len(clitics)}}}$", "", word)
        if bare != word and len(bare) >= _MIN_STEM:
            word = bare
            found.append(word)
    if _morph_values(token.morph, "Person[psor]"):
        suffix = _POSSESSIVE_RE.search(word)
        if suffix is not None and suffix.start() >= _MIN_STEM:
            found.append(word[: suffix.start()] + "n")
    return found
