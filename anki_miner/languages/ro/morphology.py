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

``ro_normalize`` also reads the letters Romanian never writes as the ones they stand for: U+00E3/U+00E5/U+01CE
(a with tilde, ring, caron) as ``ă``, U+00BA/U+00AA (the ordinal indicators) as ``ș``/``Ș`` and U+00FE/U+00DE
(thorn) as ``ț``/``Ț``, capitals alike. A cp1250 or ISO-8859-16 file read as cp1252 and saved as UTF-8 writes
``să și ați`` with those letters (the mis-encoded ``să`` is 0.43 % as common as ``să`` in OpenSubtitles ro),
and Video/Manga OCR reads ``ă`` as the tilde or ring a. Unread, such words find no definition: a mojibake file
mined 302 cards against 464 clean, and 464 identical ones with the fold; OCR fronts with a misread ``ă`` went
from 22 to 0. Only ``normalize`` reads them; the key fold and the tagger copy keep the one-to-one cedilla table.

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

``RO_LEADING_WORDS``: the article, the infinitive's ``a`` and the reflexive a deck front carries and the
mined lemma never does (S3, the de ``sich``/nl ``zich``/fr ``se`` shape): ``a se bucura`` and ``a-și
aminti`` meet the mined ``bucura`` and ``aminti``. A cedilla ``a-și`` folds through the key fold first.
``citation_rung`` drops the same words for a lookup that misses, so a note looked up by its front (Card Backfill)
finds ``vorbi`` for this profile's own verb front ``a vorbi`` and for a deck's ``a se bucura``.

``RO_GRAMMAR_SOURCES``: Romanian has neuter nouns (masculine in the singular, feminine in the plural) and
UD RRT has no neuter, so the model tags ``scaun`` Masc and ``scaunele`` Fem. The dictionary's chip or head
line decides, and the hook's own labels name all three genders, which no article pair can. The model's
morph never does: it answered only where the shown entry had no single gender, and there it was the
agreement gender of a non-noun (``hai``, ``diseară``, ``frige``) or the masculine of a neuter (``timp``,
``ceai``); 30 of 33 such genders were wrong or misleading.

``RO_SUBTITLE_REGEX``: the Latin parts with the Romanian speaker rule and the unspaced dash rule (the pl/lt
shape). ``-Unde ești? -Aici.``: spaCy keeps ``-Unde`` one token, so the spaced-only rule lost the turn's
first word, 31 of 162 fronts in the probe's dialogue.

``main_verb_pos``: with no morphologizer, the attribute ruler gives each tag its majority UPOS in training,
and 11 main-verb tags (``Vmip1s``, ``Vmip3s``, ``Vmg`` ...) come out AUX: 590 of 1,729 gold VERB tokens in
the RRT test set would be dropped as auxiliaries. Every RRT auxiliary is a ``Va`` tag, so a ``Vm`` tag lifts
the token back to VERB (19 left behind, 7 of 615 auxiliaries lifted).

``clitic_piece_pos``: a hyphen clitic split off its host (``Ce-`` in ``Ce-am``, ``-te`` in ``Oprește-te``)
and a lone letter (YouTube captions write ``s-a``/``v-am`` as ``s a``/``v am``; the interjection ``A,``)
come out NOUN (the letters ``Ncms-n``) and were mined as ``ce``, ``tu`` and "the Nth letter of the Romanian alphabet". A
content token with a hyphen edge is a pronoun piece (PRON), and a one-letter NOUN/ADJ/ADV/PROPN is X. No
real verb piece carries the hyphen (``Dă``/``-mi``), and the one-letter copula ``e`` (VERB, mined ``fi``)
is left alone. On 5 YouTube tracks (1,973 lines) 14 junk fronts go and no right card is lost. The other copula
clitic, ``-i`` in ``Ce-i``/``Unde-i``/``Nu-i``, arrives bare, since its host keeps the hyphen (``Ce-``), and
tagged VERB with the lemma ``i``, which fronted "the eleventh letter of the Romanian alphabet": it is ``fi``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    MUSIC_PATTERN,
    NORDIC_DIALOGUE_DASH_PATTERN,
    PARENS_PATTERN,
)
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

#: Leading words a deck front carries that the mined lemma never does (S3, E.1 D18), reflexives included.
RO_LEADING_WORDS: frozenset[str] = frozenset({"a", "un", "o", "niște", "se", "a-și", "a-i"})


def citation_rung(word: str, surface: str) -> list[str]:
    """``LatinLookupStrategy`` rung: a front written with its leading words (``a vorbi``, ``o carte``) offers the word."""
    del surface  # the front carries the words; a token surface never does
    tokens = word.split()
    lead = 0
    while len(tokens) - lead > 1 and tokens[lead].casefold() in RO_LEADING_WORDS:
        lead += 1
    return [" ".join(tokens[lead:])] if lead else []


RO_GRAMMAR_SOURCES: tuple[str, ...] = ("chips", "head")

#: Legacy cedilla -> standard comma-below (R35, D5). The key fold, ``normalize`` and the tagger copy share it.
RO_CEDILLA_FOLD: Mapping[str, str] = MappingProxyType({"\u015f": "ș", "\u0163": "ț", "\u015e": "Ș", "\u0162": "Ț"})
_CEDILLA_TABLE = str.maketrans(dict(RO_CEDILLA_FOLD))
#: ``normalize`` only: the cedillas plus the mojibake and OCR letters Romanian never writes (a tilde, ring or caron
#: -> ă; the ordinal indicators -> ș Ș; thorn -> ț Ț).
_NORMALIZE_TABLE = str.maketrans(
    {
        "\u00a0": " ",
        "\u00ad": None,
        **RO_CEDILLA_FOLD,
        "\u00e3": "ă",
        "\u00c3": "Ă",
        "\u00e5": "ă",
        "\u00c5": "Ă",
        "\u01ce": "ă",
        "\u01cd": "Ă",
        "\u00ba": "ș",
        "\u00aa": "Ș",
        "\u00fe": "ț",
        "\u00de": "Ț",
    }
)

#: Romanian speaker cues (``STEFAN:``, ``MARIA:``): the Latin rule with the letters Latin-1's A-Th lacks.
#: The filter runs after ``ro_normalize``, so a cedilla label already arrives comma-below.
RO_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞĂȘȚ]" r"[A-ZÀ-ÖØ-ÞĂȘȚ0-9 .'-]*" r"[A-ZÀ-ÖØ-ÞĂȘȚ]:\s*"
#: The S10 default for Romanian: the Latin parts with the Romanian speaker rule and the unspaced dash rule
#: (``-Unde ești? -Aici.``: spaCy keeps ``-Unde`` one token, so the spaced-only rule lost the word in 23 of 30
#: probe cues). No inline flags.
RO_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, RO_SPEAKER_PATTERN, NORDIC_DIALOGUE_DASH_PATTERN)
)


def ro_fold_cedilla(text: str) -> str:
    """``CasefoldDictKeys.extra_fold``: the cedilla letters to their comma-below spellings."""
    return text.translate(_CEDILLA_TABLE)


def ro_normalize(text: str) -> str:
    """S5 for Romanian: NFC; NBSP -> space; soft hyphens removed; cedillas -> comma-below (R35); mojibake and
    OCR letters Romanian never writes -> ă ș ț."""
    return unicodedata.normalize("NFC", text).translate(_NORMALIZE_TABLE)


def main_verb_pos(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """``build_spacy_tagger`` post-pass: an AUX token with a main-verb (``Vm``) tag is a VERB."""
    for token in tokens:
        if token.feature.pos1 == "AUX" and token.feature.pos2.startswith("Vm"):
            token.feature.pos1 = "VERB"
    return tokens


#: The classes a hyphen piece or a lone letter is mis-tagged into. VERB has a one-letter member, the copula ``e``.
_HYPHEN_PIECE_POS = frozenset({"NOUN", "ADJ", "ADV", "VERB", "PROPN"})
_LONE_LETTER_POS = frozenset({"NOUN", "ADJ", "ADV", "PROPN"})


def clitic_piece_pos(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """``build_spacy_tagger`` post-pass after ``main_verb_pos``: a hyphen-edged piece is PRON, a lone letter X."""
    for token in tokens:
        surface, feature = token.surface, token.feature
        if feature.pos1 in _HYPHEN_PIECE_POS and len(surface) > 1 and (surface[0] == "-" or surface[-1] == "-"):
            feature.pos1 = "PRON"
        elif feature.pos1 in _LONE_LETTER_POS and len(surface) == 1 and surface.isalpha():
            feature.pos1 = "X"
        elif feature.pos1 == "VERB" and surface.lower() == "i":
            feature.lemma = "fi"
    return tokens
