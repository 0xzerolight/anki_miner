"""Romanian data for the shared spaCy substrate (spec Appendix E, ro column).

Pinned on real ``ro_core_news_sm`` 3.8.0 output (``tests/fixtures/ro/``), UD Romanian RRT v2.8 (statistics
only) and wty-ro-en 2026.08.29.

``RO_CEDILLA_FOLD``: U+015F/U+0163/U+015E/U+0162 are the legacy cedilla spellings of the comma-below
letters U+0219/U+021B/U+0218/U+021A; neither NFC nor ``casefold()`` unifies them (R35). One table, three
uses: ``ro_normalize`` rewrites subtitle and book text, ``ro_fold_cedilla`` is the dictionary key fold's
``extra_fold`` (index and query alike, so no lookup rung exists), and the tokenizer tags a folded copy
(``tag_char_map``) because the model was trained on comma-below text. Tagging RRT test sentences re-spelt
with cedillas drops content-word lemma accuracy from 91.7 % to 84.2 %, and the frequency-list lemmatiser
calls the tagger without ``normalize``: 93 % of the OpenSubtitles list's cedilla-or-comma occurrences are
cedillas.

``RO_EXCLUDED_SUBTYPES``: the RRT MSD tags the attribute ruler maps to an allowed UPOS that are never
vocabulary: ``Rc`` (``ca``, ``iar``, ``decat``: conjunctive adverbs, SCONJ/CCONJ in other treebanks) and the
abbreviation tags (``km``, ``dl``, ``UE`` -> ``Uniunea_Europeana``). ``Rp`` (``mai``, ``foarte``), ``Rw``
(``cand``, ``unde``) and ``Rz`` (``niciodata``) stay mineable, like their English counterparts.

``RO_ABBREVIATIONS``: spaCy's Romanian tokenizer exceptions that end in ``.``, casefolded without the final
dot, with spaCy's own diacritic-less variants (``samd``, ``s.a.m.d``, ``st``) and without cedilla variants
(the tagger reads a comma-below copy). Left out, because they end sentences as ordinary words in subtitles:
``ex`` (an ex), ``rom`` (rum), ``ian`` (the name Ian). Added: ``dl``, ``dna``, ``dra``, titles written before
a name (``Dl. Popescu``) that the model has no exception for, so only the sentence splitter reads them. The
vocative ``dle`` ends sentences and is not listed.

``RO_GRAMMAR_SOURCES``: Romanian has neuter nouns (masculine in the singular, feminine in the plural) and
UD RRT has no neuter, so the model tags ``scaun`` Masc and ``scaunele`` Fem. The dictionary's chip or head
line leads, and the hook's own labels name all three genders, which no article pair can.

``main_verb_pos``: with no morphologizer, the attribute ruler gives each tag its majority UPOS in training,
and 11 main-verb tags (``Vmip1s``, ``Vmip3s``, ``Vmg`` ...) come out AUX: 590 of 1,729 gold VERB tokens in
the RRT test set would be dropped as auxiliaries. Every RRT auxiliary is a ``Va`` tag, so a ``Vm`` tag lifts
the token back to VERB (19 left behind, 7 of 615 auxiliaries lifted).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import BRACKETS_PATTERN, DIALOGUE_DASH_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages.token import LanguageToken

#: The model package the tokenizer loads and the availability probe looks for.
RO_MODEL_PACKAGE = "ro_core_news_sm"

RO_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
RO_EXCLUDED_SUBTYPES: tuple[str, ...] = ("Rc", "Ya", "Yn", "Ynfsoy", "Ynfsry", "Ynmsoy", "Ynmsry", "Yr")

RO_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles and names
        "dr", "prof", "ing", "dvs", "sf", "șt", "st", "gh", "al", "lt", "dl", "dna", "dra",
        # references and units
        "nr", "art", "alin", "lit", "pct", "fig", "obs", "tel", "str", "bd", "prep", "univ",
        "îngr", "ingr", "dem", "fr", "gr", "aug",
        # multi-dot
        "etc", "d.p.d.v", "ș.a.m.d", "s.a.m.d", "șamd", "samd", "ș.a", "s.a",
        "a.c", "a.f", "a.r", "a.m", "p.a", "p.m",
    }
)  # fmt: skip

#: Leading words a deck front carries that the mined lemma never does (S3, E.1 D18).
RO_LEADING_WORDS: frozenset[str] = frozenset({"a", "un", "o", "niște"})

RO_GRAMMAR_SOURCES: tuple[str, ...] = ("chips", "head", "morph")

#: Legacy cedilla -> standard comma-below (R35, D5). The key fold, ``normalize`` and the tagger copy share it.
RO_CEDILLA_FOLD: Mapping[str, str] = MappingProxyType({"\u015f": "ș", "\u0163": "ț", "\u015e": "Ș", "\u0162": "Ț"})
_CEDILLA_TABLE = str.maketrans(dict(RO_CEDILLA_FOLD))
_NORMALIZE_TABLE = str.maketrans({"\u00a0": " ", "\u00ad": None, **RO_CEDILLA_FOLD})

#: Romanian speaker cues (``STEFAN:``, ``MARIA:``): the Latin rule with the letters Latin-1's A-Th lacks.
#: The filter runs after ``ro_normalize``, so a cedilla label already arrives comma-below.
RO_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞĂȘȚ]" r"[A-ZÀ-ÖØ-ÞĂȘȚ0-9 .'-]*" r"[A-ZÀ-ÖØ-ÞĂȘȚ]:\s*"
#: The S10 default for Romanian: the Latin parts with the Romanian speaker rule. No inline flags.
RO_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, RO_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)


def ro_fold_cedilla(text: str) -> str:
    """``CasefoldDictKeys.extra_fold``: the cedilla letters to their comma-below spellings."""
    return text.translate(_CEDILLA_TABLE)


def ro_normalize(text: str) -> str:
    """S5 for Romanian: NFC; NBSP -> space; soft hyphens removed; cedillas -> comma-below (R35)."""
    return unicodedata.normalize("NFC", text).translate(_NORMALIZE_TABLE)


def main_verb_pos(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """``build_spacy_tagger`` post-pass: an AUX token with a main-verb (``Vm``) tag is a VERB."""
    for token in tokens:
        if token.feature.pos1 == "AUX" and token.feature.pos2.startswith("Vm"):
            token.feature.pos1 = "VERB"
    return tokens
