"""Danish data for the shared spaCy substrate.

Pinned on real ``da_core_news_sm`` 3.8.0 output (``tests/fixtures/da/``) and wty-da-en 2026.08.29 rows.

``DA_EXCLUDED_SUBTYPES`` is empty: the model has no ``tagger``, so ``tag_ == pos_`` and every token's ``pos2`` is
``""`` (E.10 D11). The fine-tag gate is dead config for Danish, as for nb, ko, ru and uk.

Particle verbs: wty-da-en keys them as two words, verb first (``stå op``, ``give op``, ``se ud``), so the join is
``lemma + " " + particle`` and never the fused ``opstå`` (arise), which is another verb. ``DA_SEPARABLE_VERB_DEPS``
is UD's dedicated ``compound:prt`` plus the adverbial arcs, because the model puts most Danish particles on
``advmod``/``advmod:lmod`` (``Hun stod op``, ``Giv ikke op``, ``Du ser træt ud``): over the 2,995 example sentences
of wty-da-en those arcs attest 50 more two-word verbs (11 -> 61). They also carry every other adverb of the verb
(``ikke``, ``nu``, ``tidligt``), so ``DA_ADVERBIAL_PARTICLE_DEPS`` join only when the dictionary attests the
two-word verb, never blindly without one; ``SeparableVerbPass`` demotes only the particle it joins, and every other
adverb stays a word.

``DA_ARTICLE_MAP`` / ``DA_GRAMMAR_SOURCES``: two genders, common (``en``) and neuter (``et``). wty-da-en writes the
gender letter in the Grammar head line (``bog c (...)``, ``hus n (...)``) and tags only neuter rows with a chip, and
the head line leads: over 3,702 NOUN tokens whose lemma has one dictionary gender, the model's ``Gender=`` first put
the wrong article on 85 (``jakke``, ``sygdom`` and ``kalv`` are tagged neuter), the head line first on 8.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    LATIN_SPEAKER_PATTERN,
    MUSIC_PATTERN,
    NORDIC_DIALOGUE_DASH_PATTERN,
    PARENS_PATTERN,
    nfc_normalize,
)
from anki_miner.languages.token import LanguageToken

#: The model package the tokenizer loads and the availability probe looks for.
DA_MODEL_PACKAGE = "da_core_news_sm"

DA_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
DA_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: The adverbial arcs the model hangs most Danish particles on; a join there needs the dictionary (module docstring).
DA_ADVERBIAL_PARTICLE_DEPS: frozenset[str] = frozenset({"advmod", "advmod:lmod"})
#: UD's dedicated verb-particle arc and the adverbial ones.
DA_SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"compound:prt"}) | DA_ADVERBIAL_PARTICLE_DEPS


def danish_particle_candidates(token: Any) -> list[str]:
    """``SeparableVerbPass`` candidates for a verb head carrying ``feature.particle``: the two-word headword."""
    return [f"{token.feature.lemma} {token.feature.particle}"]


def ikke_as_particle(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass (``build_spacy_tagger(post_passes=...)``): the negation ``ikke`` becomes PART.

    ``da_core_news_sm`` tags it ADV, which mines, so one of the five commonest Danish words reached a card; the nb
    and sv models tag the same negation (``ikke``, ``inte``) PART, and fr retags its ``ne`` the same way.
    """
    for token in tokens:
        if token.feature.pos1 == "ADV" and token.feature.lemma == "ikke":
            token.feature.pos1 = "PART"
    return tokens


#: Leading words a deck front carries that the mined lemma never does (S3): ``en bog``, ``et hus``, ``at gå``.
DA_LEADING_WORDS: frozenset[str] = frozenset({"en", "et", "at"})

DA_ARTICLE_MAP: Mapping[str, str] = MappingProxyType({"masc": "en", "fem": "en", "common": "en", "neut": "et"})
DA_GRAMMAR_SOURCES: tuple[str, ...] = ("head", "chips", "morph")

#: Danish quotes »...« and „...“; the shared Latin set opens with « and “, which Danish closes with.
DA_OPENERS: frozenset[str] = frozenset("([{„»")
DA_CLOSERS: frozenset[str] = frozenset(")]}“«")

#: The S10 default for Danish: the shared Latin parts with the shared Nordic dash rule. No inline flags.
#: Danish subtitles write the speaker dash unspaced (``-Bogen ligger her.``), which the shipped Latin rule
#: leaves glued to the word; ``NORDIC_DIALOGUE_DASH_PATTERN`` (nb shipped it first, sv consumes it too) also
#: accepts a letter directly after the dash, so ``-5 grader ude.`` keeps its minus sign.
DA_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, NORDIC_DIALOGUE_DASH_PATTERN)
)

_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None})


def da_normalize(text: str) -> str:
    """S5 for Danish: NFC; NBSP -> space; soft hyphens (e-book hyphenation points) removed. æ, ø, å never change."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)
