"""Lithuanian data for the shared spaCy substrate (spec Appendix E, lt column).

Evidence: real ``lt_core_news_sm`` 3.8.0 output through ``build_spacy_tagger`` over UD Lithuanian ALKSNIS r2.8
dev+test (1,301 held-out sentences, statistics only) and the 1,951 example sentences of wty-lt-en 2026.08.29.

``strip_stress_marks``: learner texts and dictionaries mark stress with a grave, acute or tilde (``knygà``,
``knỹgos``); an accented ``i`` or ``j`` keeps a combining dot above. The marks are not orthography, the model
cannot tag them (``Knygà yrà añt stãlo.`` tags ``Knygà`` as an infinitive), and wty-lt-en keys all 9,215 lemma rows
without them while 112,100 of its 188,425 inflected-form rows carry them. So the fold runs in ``lt_normalize``
(the mined text) and in the dictionary key fold (both index ends, and so the known-word fold). The letters' own
diacritics (``ą č ę ė į š ų ū ž``) are never touched. The fold is unconditional, so a foreign word inside a
Lithuanian line loses its accent in the stored card sentence too (``José`` → ``Jose``, ``crème`` → ``creme``): a
knowing trade, since Lithuanian orthography never writes those marks and no wty-lt-en lemma key carries one.

``LT_EXCLUDED_SUBTYPES`` is empty on evidence (D17): the ALKSNIS tagger (``tag_acc`` .82) hangs pronoun, multiword,
foreign and proper-noun tags on real content words (``jaunas``, ``miškuose``); no fine tag under
NOUN/VERB/ADJ/ADV was reliably non-vocabulary once abbreviations became single tokens.

``LT_ABBREVIATIONS`` (S8, D8): spaCy's lt exceptions drop every dotted abbreviation and vlkk.lt is
Cloudflare-gated, so the set is derived from ``tests/fixtures/lt/abbreviations.py`` (en.wiktionary's
Category:Lithuanian abbreviations, the dotted ``sutr.`` tokens of ALKSNIS, every letter, 17 hand-written stems;
the test re-derives it). The same set is the tokenizer's special cases (``lt/tokenizer.py``).

Quotes: Lithuanian opens with ``„`` and closes with ``“`` (ALKSNIS text: 498 ``„``, 479 ``“``, 15 ``”``), so ``“``
never opens a quote here (the de shape).

``LT_SUBTITLE_REGEX``: the Latin SDH filter with a speaker-label class that includes the Lithuanian capitals;
the Latin class stops at Latin-1 and leaves ``ŠARŪNAS:`` in the card sentence (the fr precedent).
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
)

#: The model package the tokenizer loads and the availability probe looks for.
LT_MODEL_PACKAGE = "lt_core_news_sm"

LT_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
LT_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

LT_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "a", "akad", "al", "angl", "aps", "apskr", "aut", "b", "bal", "bdv", "birž", "buv", "c", "d", "dem",
        "dgs", "dkt", "dll", "doc", "dr", "dvs", "e", "el", "ež", "f", "faks", "g", "geg", "gen", "gerb",
        "gim", "gruod", "gub", "gyv", "h", "habil", "i", "inž", "j", "jng", "k", "kg", "kov", "kr", "kt",
        "kun", "kuop", "l", "lapkr", "liep", "m", "m.m", "menk", "min", "mln", "mlrd", "mot", "mst", "mstl",
        "mėn", "n", "naud", "nr", "o", "p", "pagr", "pan", "par", "past", "pav", "pl", "plg", "ppr", "pr",
        "pranc", "prl", "proc", "prof", "prv", "psl", "pvz", "q", "r", "red", "rugp", "rugs", "rus", "s",
        "saus", "sav", "saviv", "sb", "sen", "sk", "skait", "spal", "str", "t", "t.t", "t.y", "tel", "trln",
        "tūkst", "u", "v", "vad", "val", "vard", "vas", "viet", "vks", "vlsč", "vns", "vnt", "vok", "vt",
        "vyr", "vyresn", "vysk", "w", "x", "y", "z", "ą", "č", "ė", "ę", "į", "įn", "įnag", "įv", "š", "š.m",
        "šauksm", "šnek", "šv", "švč", "ū", "ų", "ž", "žin", "žr",
    }
)  # fmt: skip

#: „…“ with guillemets and brackets; “ closes (Lithuanian), so it is not an opener.
LT_OPENERS: frozenset[str] = frozenset("([{„«")
LT_CLOSERS: frozenset[str] = frozenset(")]}“”»")

_CAPITALS = "A-ZÀ-ÖØ-ÞĄČĘĖĮŠŲŪŽ"
#: ``JONAS:``, ``ŠARŪNAS:`` — two or more capitals, Lithuanian ones included, then a colon at the cue start.
LT_SPEAKER_PATTERN = rf"^[{_CAPITALS}][{_CAPITALS}0-9 .'-]*[{_CAPITALS}]:\s*"
#: The S10 default for Lithuanian: the Latin parts with the Lithuanian speaker rule. No inline flags.
LT_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LT_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

_STRESS_MARKS = frozenset("\u0300\u0301\u0303")
_DOT_ABOVE = "\u0307"
_SOFT_DOTTED = frozenset("iIjJ")
_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None})


def strip_stress_marks(text: str) -> str:
    """NFD, drop grave/acute/tilde and a dot above ``i``/``j``, NFC. Idempotent; letters' diacritics stay."""
    out: list[str] = []
    base = ""
    for char in unicodedata.normalize("NFD", text):
        if not unicodedata.category(char).startswith("M"):
            base = char
        elif char in _STRESS_MARKS or (char == _DOT_ABOVE and base in _SOFT_DOTTED):
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def lt_normalize(text: str) -> str:
    """``LanguageProfile.normalize`` (S5): stress marks off (NFC out), NBSP → space, soft hyphens removed."""
    return strip_stress_marks(text).translate(_NORMALIZE_MAP)
