"""Norwegian Bokmål data for the shared spaCy substrate.

Pinned on real ``nb_core_news_sm`` 3.8.0 output (``tests/fixtures/nb/``) and wty-nb-en 2026.08.29 rows.

The model has no ``tagger`` component, so ``tag_ == pos_`` on every token: ``pos2`` is always "" and
``NB_EXCLUDED_SUBTYPES`` is dead config (spec D11, the ko/ca shape; ``test_nb_pos_corpus.py`` pins it).

``norwegian_particle_candidates``: a Norwegian particle verb is written apart and Wiktionary keys it apart
(``stå opp``, ``ta opp``, ``laste ned``), so the one candidate is ``lemma + " " + particle``. The shared default,
particle + lemma, attests a different verb on 8 of 30 real arcs (``står … opp`` → ``oppstå`` "arise", ``tok …
opp`` → ``oppta`` "occupy", ``la … på`` → ``pålegge`` "impose"). A particle that is not a word offers no join:
``kl.`` carries the arc in ``begynner kl. 9``.

``NB_ARTICLE_MAP``: indefinite articles, keyed like wty's masc/fem/neut chips and the model's Masc/Fem/Neut. A
feminine noun takes ``ei`` or ``en`` in Bokmål (``bok f or m``), so the token's own ``Gender=`` decides (``boka`` →
``ei``, ``boken`` → ``en``, spec E.2.2) and ``GrammarTagHook`` keeps its default source order.

``NB_SUBTITLE_REGEX``: Norwegian subtitles mark a second speaker with a hyphen and no space (``-Kom hit. -Nei.``,
spec E.4). spaCy glues that hyphen to the word (``-Kom`` is one token, so the verb never mines), and the Latin
dialogue-dash rule needs a space; the Nordic rule (``NORDIC_DIALOGUE_DASH_PATTERN``, shared with sv and da since
this shape shipped here first) also takes a dash followed directly by a letter. A dash that follows
a speaker label (``OLA: -Hvor er du?``) stays out of reach: the shared speaker pattern consumes ``OLA: `` first, so
the sentence-start anchor no longer matches (known limit, not fixed here — the repair is in the shared pattern).
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

#: The model package the tokenizer loads and the availability probe looks for.
NB_MODEL_PACKAGE = "nb_core_news_sm"

NB_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
NB_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: UD Norwegian labels a verb particle ``compound:prt`` (spec §4.3 item 2, E.2.2).
NB_SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"compound:prt"})


def norwegian_particle_candidates(token: Any) -> list[str]:
    """``SeparableVerbPass`` candidates: the verb lemma and its particle, written apart; none for a non-word."""
    particle: str = token.feature.particle
    if not particle.isalpha():
        return []
    return [f"{token.feature.lemma} {particle}"]


#: Leading words a deck front carries that the mined lemma never does (S3): ``en bok`` meets ``bok``.
NB_LEADING_WORDS: frozenset[str] = frozenset({"en", "ei", "et", "å"})

NB_ARTICLE_MAP: Mapping[str, str] = MappingProxyType({"masc": "en", "fem": "ei", "neut": "et"})

#: A speaker dash at the cue start or after a sentence end, followed by spaces or directly by a letter. Shared
#: with the other Nordic languages since sv promoted it (same bytes; the shape shipped here first).
NB_DIALOGUE_DASH_PATTERN = NORDIC_DIALOGUE_DASH_PATTERN
#: The S10 default for Norwegian: the Latin parts with the unspaced dash rule. No inline flags.
NB_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, NB_DIALOGUE_DASH_PATTERN)
)

_NORMALIZE_MAP = str.maketrans({"\N{NO-BREAK SPACE}": " ", "\N{SOFT HYPHEN}": None})


def nb_normalize(text: str) -> str:
    """S5 for Norwegian Bokmål: NFC; NBSP → space; soft hyphens (e-book hyphenation points) removed."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)
