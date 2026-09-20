"""Russian data for the shared spaCy substrate (spec Appendix B ru column; plan D3, D7-D10).

``strip_stress`` / ``ru_normalize``: learner texts and some subtitles mark stress with an acute (rarely
a grave): a marked ``читал книгу`` tags as two PROPN tokens unless the marks go. Only a mark on a
Cyrillic letter goes, so ``café`` in a Russian line keeps its accent; й and ё are letters (breve,
diaeresis) and stay.

``RU_KEYS``: a dictionary term folds case, stress and ё->е (texts write е for ё at will), the lt shape.
A reading keeps its stress and its ё — they are the point of the reading — except the acute on a
one-vowel word, which is redundant and splits one reading in two (OpenRussian writes дом, wty marks
it): over the 1,500 most frequent content lemmas the fold takes S24's ambiguous count from 155 to 52.

``RU_EXCLUDED_SUBTYPES`` is empty: ``ru_core_news_sm`` has no fine tagset (``tag_ == pos_``), so
``pos2`` is always ``""``.

``RU_SENTENCE_RULES``: Russian quotes with guillemets and, inside those, the low quote, which the left
double quote closes — so that quote leaves the shared openers (where it is the English opener) for the
closers.
"""

from __future__ import annotations

import dataclasses
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.pymorphy import PymorphyLemmaRepair
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
    strip_cyrillic_stress,
)
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.ru.abbreviations import RU_ABBREVIATIONS

RU_MODEL_PACKAGE = "ru_core_news_sm"
#: What spaCy's Russian lemmatizer imports beside the model; both travel in the ru pack (plan D4, D15).
RU_MORPHOLOGY_PACKAGES: tuple[str, ...] = ("pymorphy3", "pymorphy3_dicts_ru")

RU_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
RU_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: The model's tagging copy (plan D7): it saw almost no yo in training (ушёл tagged NOUN).
RU_TAG_CHAR_MAP: Mapping[str, str] = MappingProxyType({"ё": "е", "Ё": "Е"})

_NORMALIZE_MAP = str.maketrans({"\N{NO-BREAK SPACE}": " ", "\N{SOFT HYPHEN}": None})
_VOWELS = frozenset("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
_ACUTE = "\N{COMBINING ACUTE ACCENT}"

#: Moved to _spaced/script.py when uk needed the same function; re-exported so ru's call sites and
#: tests keep the name they were written against.
strip_stress = strip_cyrillic_stress


def ru_normalize(text: str) -> str:
    """``LanguageProfile.normalize`` (S5): stress marks off, NBSP -> space, soft hyphens removed."""
    return strip_stress(text).translate(_NORMALIZE_MAP)


def ru_key_fold(text: str) -> str:
    """The term fold before casefold: stress off, ё -> е. Idempotent."""
    return strip_stress(text).replace("ё", "е").replace("Ё", "Е")


class RuDictKeys(CasefoldDictKeys):
    """Russian ``DictKeyFolding``: the Latin term fold with ``ru_key_fold``; readings keep stress and ё."""

    def fold_reading(self, s: str | None) -> str | None:
        if s is None:
            return None
        reading = unicodedata.normalize("NFC", s)
        if sum(char in _VOWELS for char in reading) == 1:
            return reading.replace(_ACUTE, "")
        return reading


RU_KEYS = RuDictKeys(extra_fold=ru_key_fold)
#: S3: known words, blacklist, whitelist and dedup compare through the key fold (no leading words).
RU_DEDUP_FOLD = spaced_dedup_fold(RU_KEYS)


class StressedHeadwordReading:
    """``ReadingSupport`` (spec B.2): ru owns its reading fields and has no reading of its own.

    ``word_reading`` answers ``""``; with ``attested_reading_fallback`` (S24, set by ``ru/parser.py``) the
    parser then takes the one stressed headword the dictionary attests. Without a support the JA-shaped
    derivation would run and print the stressed spelling as furigana.
    """

    def word_reading(self, token: Any) -> str:
        return ""


_BASE_RULES = sentence_rules(RU_ABBREVIATIONS)
RU_SENTENCE_RULES = dataclasses.replace(
    _BASE_RULES,
    openers=(_BASE_RULES.openers - {"“"}) | {"„"},
    closers=_BASE_RULES.closers | {"“"},
)

#: ``ИВАН:``, ``МАША ПЕТРОВА:`` — the shared Latin speaker rule with the Russian capitals.
RU_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞЁА-Я][A-ZÀ-ÖØ-ÞЁА-Я0-9 .'-]*[A-ZÀ-ÖØ-ÞЁА-Я]:\s*"
#: The S10 default for Russian: the shared parts with the Russian speaker rule. No inline flags.
RU_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, RU_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

#: noun_gender labels (spec §4.8), as Russian dictionaries abbreviate the three genders.
RU_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "м.", "fem": "ж.", "neut": "с."})


def _yo_blind(text: str) -> str:
    return text.lower().replace("ё", "е")


class RuLemmaRepair(PymorphyLemmaRepair):
    """The shared pymorphy3 repair with Russian's yo-blind identity fold (plan D7).

    The tagging copy writes е for ё, so ``ушёл`` reaches the lemmatiser as ``ушел`` and its lemma
    comes back identical to that copy's surface: only a yo-blind comparison sees it. The ambiguous
    fallback stays the lower-cased ORIGINAL surface — that is what restores the yo the copy took
    out, which is why ru passes no ``analysis_form`` and keeps the identity default.
    """

    def __init__(self) -> None:
        super().__init__(allowed_pos=RU_ALLOWED_POS, fold=_yo_blind)
