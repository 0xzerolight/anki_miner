"""Danish data for the shared spaCy substrate.

Pinned on real ``da_core_news_sm`` 3.8.0 output (``tests/fixtures/da/``) and wty-da-en 2026.08.29 rows.

``DA_EXCLUDED_SUBTYPES`` is empty: the model has no ``tagger``, so ``tag_ == pos_`` and every token's ``pos2`` is
``""`` (E.10 D11). The fine-tag gate is dead config for Danish, as for nb, ko, ru and uk.

Particle verbs: wty-da-en keys them as two words, verb first (``stå op``, ``give op``, ``se ud``), so the join is
``lemma + " " + particle`` and never the fused ``opstå`` (arise), which is another verb. ``DA_SEPARABLE_VERB_DEPS``
is UD's dedicated ``compound:prt`` and nothing else, the nb and nl shape. The model in fact puts most Danish
particles on ``advmod``/``advmod:lmod``: over the 2,995 example sentences of wty-da-en those arcs would attest 50
more two-word verbs (11 -> 61), but ``to_duck_tokens`` demotes a stashed token to ``PART`` before the dictionary is
consulted and ``SeparableVerbPass`` never restores it, so they would also drop ~170 ordinary adverbs (``ud``,
``op``, ``ind``, ``sammen``) out of mining. **Known limit:** a particle the parser labels ``advmod`` leaves its verb
front bare -- ``gik ud`` mines ``gå``, not ``gå ud``.

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
    PARENS_PATTERN,
    nfc_normalize,
)

#: The model package the tokenizer loads and the availability probe looks for.
DA_MODEL_PACKAGE = "da_core_news_sm"

DA_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
DA_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: UD's dedicated verb-particle arc. The adverbial arcs are deliberately out (see the module docstring).
DA_SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"compound:prt"})


def danish_particle_candidates(token: Any) -> list[str]:
    """``SeparableVerbPass`` candidates for a verb head carrying ``feature.particle``: the two-word headword."""
    return [f"{token.feature.lemma} {token.feature.particle}"]


#: Leading words a deck front carries that the mined lemma never does (S3): ``en bog``, ``et hus``, ``at gå``.
DA_LEADING_WORDS: frozenset[str] = frozenset({"en", "et", "at"})

DA_ARTICLE_MAP: Mapping[str, str] = MappingProxyType({"masc": "en", "fem": "en", "common": "en", "neut": "et"})
DA_GRAMMAR_SOURCES: tuple[str, ...] = ("head", "chips", "morph")

#: Danish quotes »...« and „...“; the shared Latin set opens with « and “, which Danish closes with.
DA_OPENERS: frozenset[str] = frozenset("([{„»")
DA_CLOSERS: frozenset[str] = frozenset(")]}“«")

#: A speaker dash at the cue start or after a sentence end, followed by spaces OR directly by a letter.
#: Danish subtitles write it unspaced (``-Kom her.``), which the shared rule leaves glued to the word.
DA_DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…]\s))[-–—](?:\s+|(?=[^\W\d_]))"
#: The S10 default for Danish: the Latin parts with the unspaced dash rule. No inline flags.
DA_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, DA_DIALOGUE_DASH_PATTERN)
)

_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None})


def da_normalize(text: str) -> str:
    """S5 for Danish: NFC; NBSP -> space; soft hyphens (e-book hyphenation points) removed. æ, ø, å never change."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)
