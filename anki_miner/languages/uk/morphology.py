"""Ukrainian data for the shared spaCy substrate (spec Appendix B uk column; plan P3-P10).

``uk_normalize``: stress marks off (a marked ``кни<acute>жка чита<acute>ла`` tags NUM + NOUN with
identity lemmas; stripped it tags NOUN + VERB), NBSP to a space, soft hyphens removed. The
apostrophe is deliberately NOT folded here -- U+2019 is correct Ukrainian typography and the card
sentence keeps whichever mark the subtitle wrote.

``canonical_apostrophes`` / ``apostrophe_blind``: everything that talks to an engine or an index
sees one apostrophe, U+0027. ``pymorphy3-dicts-uk`` knows only that spelling (every parse of a
typographic ``м'яча`` comes back ``is_known=False``) and ``wty-uk-en`` spells 7,334 of its 7,337
apostrophe headwords that way, so the tagging copy, the repair's two policies and the dictionary
key fold all converge on it while the token surface stays a verbatim slice of the cleaned line.

``UK_KEYS``: a dictionary term folds case, stress and every apostrophe spelling. A reading is NFC
only -- ru's one-vowel acute fold is NOT copied, because in the whole of ``wty-uk-en`` only twelve
readings are one-vowel-with-acute and the fold changes the ambiguous count by zero (plan P8).

``UK_EXCLUDED_SUBTYPES`` is empty: ``uk_core_news_sm`` has no fine tagset (``tag_ == pos_``), so
``pos2`` is always ``""``.

``UK_SENTENCE_RULES``: Ukrainian quotes with guillemets and, inside those, the low quote, which the
left double quote closes -- so that quote leaves the shared openers (where it is the English
opener) for the closers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
    strip_cyrillic_stress,
)
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.uk.abbreviations import UK_ABBREVIATIONS

UK_MODEL_PACKAGE = "uk_core_news_sm"
#: What spaCy's Ukrainian lemmatizer imports beside the model; both travel in the uk pack. The
#: Russian dictionaries are NOT among them: ``MorphAnalyzer(lang="uk")`` resolves through the
#: ``pymorphy3_dicts`` entry-point group alone (plan P1).
UK_MORPHOLOGY_PACKAGES: tuple[str, ...] = ("pymorphy3", "pymorphy3_dicts_uk")

UK_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
UK_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

_ASCII_APOSTROPHE = "'"
#: The tagging copy's apostrophe map (plan P3). U+02B9 is left out on purpose: it is an ISO-9
#: transliteration character with no occurrence in either the dictionary or the frequency corpus.
UK_TAG_CHAR_MAP: Mapping[str, str] = MappingProxyType(
    {
        "\N{RIGHT SINGLE QUOTATION MARK}": _ASCII_APOSTROPHE,
        "\N{MODIFIER LETTER APOSTROPHE}": _ASCII_APOSTROPHE,
        "`": _ASCII_APOSTROPHE,
    }
)

_NORMALIZE_MAP = str.maketrans({"\N{NO-BREAK SPACE}": " ", "\N{SOFT HYPHEN}": None})
_APOSTROPHE_MAP = str.maketrans(dict(UK_TAG_CHAR_MAP))


def canonical_apostrophes(text: str) -> str:
    """The spelling ``pymorphy3-dicts-uk`` and ``wty-uk-en`` know: every apostrophe as U+0027."""
    return text.translate(_APOSTROPHE_MAP)


def apostrophe_blind(text: str) -> str:
    """``PymorphyLemmaRepair(fold=...)``: "the lemmatiser echoed the tagging copy", apostrophe-blind."""
    return canonical_apostrophes(text).lower()


def uk_normalize(text: str) -> str:
    """``LanguageProfile.normalize`` (S5): stress marks off, NBSP -> space, soft hyphens removed."""
    return strip_cyrillic_stress(text).translate(_NORMALIZE_MAP)


def uk_key_fold(text: str) -> str:
    """The term fold before casefold: stress off, every apostrophe spelling to U+0027. Idempotent."""
    return canonical_apostrophes(strip_cyrillic_stress(text))


#: Plan P8: no ``fold_reading`` override - the ru one-vowel acute fold earns nothing for uk.
UK_KEYS = CasefoldDictKeys(extra_fold=uk_key_fold)
#: S3: known words, blacklist, whitelist and dedup compare through the key fold (no leading words).
UK_DEDUP_FOLD = spaced_dedup_fold(UK_KEYS)


class StressedHeadwordReading:
    """``ReadingSupport`` (spec B.2, S24): uk owns the reading fields and has no reading of its own.

    ``word_reading`` answers ``""``; with ``attested_reading_fallback`` (set by ``uk/parser.py``)
    the parser then takes the one stressed headword the dictionary attests. wty-uk-en leaves its
    LEMMA rows blank but fills every non-lemma row, so the field is filled for roughly six of ten
    defined fronts and left blank for a homograph like ``замок`` (plan P7). Without a support the
    JA-shaped derivation would run and print the stressed spelling as furigana.
    """

    def word_reading(self, token: Any) -> str:
        return ""


_BASE_RULES = sentence_rules(UK_ABBREVIATIONS)
UK_SENTENCE_RULES = dataclasses.replace(
    _BASE_RULES,
    openers=(_BASE_RULES.openers - {"“"}) | {"„"},
    closers=_BASE_RULES.closers | {"“"},
)

#: ``ІВАН:``, ``ОЛЕНА ПЕТРІВНА:`` — the shared Latin speaker rule with the Ukrainian capitals.
#: Є І Ї Ґ sit outside U+0410-042F, so neither the Latin nor the Russian class can match them.
UK_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞЄІЇҐА-Я][A-ZÀ-ÖØ-ÞЄІЇҐА-Я0-9 .'’-]*[A-ZÀ-ÖØ-ÞЄІЇҐА-Я]:\s*"
#: The S10 default for Ukrainian: the shared parts with the Ukrainian speaker rule. No inline flags.
UK_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, UK_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

#: noun_gender labels (spec §4.8), as Ukrainian dictionaries abbreviate the three genders.
UK_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "ч.", "fem": "ж.", "neut": "с."})
