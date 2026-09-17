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

Clitics (``-kin -kaan -han -pa -ko -s``) and possessive suffixes (``-ni -si -nsa -mme -nne``) get no lookup rung and
no front repair. The model drops them in context (``kirjakin``, ``kirjani`` mine as ``kirja``) and keeps them
elsewhere (a sentence-initial ``Kirjakin``). A strip rung would turn 0.20 % of content tokens from a dictionary miss
into a hit while misfiring on others (``tuttuin`` -> ``tuttu``), and the parser's attestation cannot tell a stem from
another inflected form, because wty-fi-en lists inflected forms as terms (``haluatko`` -> ``haluat``).
"""

from __future__ import annotations

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
