"""Swedish data for the shared spaCy substrate.

Pinned on real ``sv_core_news_sm`` 3.8.0 output (``tests/fixtures/sv/``) and wty-sv-en 2026.08.29 rows; every count
below was measured over the dictionary's 21,028 example sentences through the shipped configuration.

``SV_EXCLUDED_SUBTYPES``: SUC tags the live tagger puts under an allowed UPOS that are a class of non-vocabulary,
each with the real-word loss it costs -- abbreviation forms (``AB|AN`` 278 tokens, mostly bare letters and
``dvs.``, losing ~12 tokens of ``era``/``eld``/``idé``/``kapitel``; ``NN|AN`` 199 tokens of ``kr``/``kg``/``km``/
``kap.``, losing ~20 tokens of ``dö``/``ed``/``fot``/``såg``), interjections (``IN`` 7 tokens: ``ja``, ``he``,
``åh``; losing ``föll`` and ``så``) and an ordinal, which lemmatises to its cardinal (``RO|NOM`` 121 tokens, lemma
``en`` alone 66 -- the nl ``TW|rang`` case; losing ~14 tokens of ``hetta``/``lära``/``skaka``/``sunt``). A stranded
verb particle (``PL``: ``upp`` in ``sprang upp för trappan``) is NOT excluded: it is 145 tokens over 42 lemmas, 39
of them dictionary headwords (``in``, ``ner``, ``upp``, ``fast``, ``ut``, ``fram``, ``igen``), and a particle that
carries a ``compound:prt`` arc is ``PART`` before the gate sees it, so excluding it only removed real adverbs.
Inflection tags stay, ``GEN`` included: the model lemmatises a genitive common noun to its base (``husets`` ->
``hus``) and a name's genitive is PROPN, so nothing there needs removing, and an excluded subtype removes a word
with no trace.

``swedish_particle_candidates``: Swedish writes a particle verb as two words, and so does the dictionary -- over
the 2,230 ``compound:prt`` arcs that sit on a head the stash keeps (``PARTICLE_HEAD_POS`` = VERB/AUX; 2,465 arcs in
total), wty-sv-en attests the spaced join 1,197 times against 577 for the concatenation, which is usually a
different verb (``ta av`` -> ``avta`` "diminish"). Only the spaced join is offered, and a non-alphabetic particle
offers none (real arcs produce ``bo ―``, ``lägga /``, ``bikini →``). 926 of those arcs (41.5 %) attest neither
join, and ``SeparableVerbPass`` then keeps the model's bare lemma: ``komma tillbaka`` mines as ``komma``, and so do
``få``, ``ta`` and ``dra`` on their unattested particles.

``SV_ARTICLE_MAP``: Swedish has two genders and one indefinite article each, so every non-neuter key maps to
``en`` (the nl "every non-neuter takes de" shape). The morphologizer's ``Gender=Com``/``Gender=Neut`` answers for
every noun; wty-sv-en's chip vocabulary is ``fem``/``masc``/``neut`` with no common chip, and 62 rows put a masc or
fem chip on an ordinary common-gender noun (``maka``, ``grip``, ``kasus``), so without those two keys the chip
would resolve to "" and suppress the ``en`` the morph already knew. The head line spells the letter
(``apa c (plural apor)``), which is also where ``noun_plural`` comes from, so the default source order (morph,
chips, head) needs no override.

``SV_SUBTITLE_REGEX``: Nordic subtitles write the speaker dash unspaced (``-Kom hit.``), where the shipped Latin
rule needs a space and spaCy glues ``-Kom`` into one PUNCT token, losing the word. The shared
``NORDIC_DIALOGUE_DASH_PATTERN`` (nb shipped it first, da consumes it too) requires a letter after an unspaced
dash, so ``-5 grader ute.`` keeps its minus sign.
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
SV_MODEL_PACKAGE = "sv_core_news_sm"

SV_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
SV_EXCLUDED_SUBTYPES: tuple[str, ...] = ("AB|AN", "IN", "NN|AN", "RO|NOM")

#: UD Swedish labels a verb particle ``compound:prt`` (spec §4.3 item 2), as UD Dutch does.
SV_SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"compound:prt"})


def swedish_particle_candidates(token: Any) -> list[str]:
    """The one join for a verb head carrying ``feature.particle``: ``lemma`` space ``particle``; none for junk."""
    particle: str = token.feature.particle
    if not particle.isalpha():
        return []
    return [f"{token.feature.lemma} {particle}"]


SV_ARTICLE_MAP: Mapping[str, str] = MappingProxyType({"common": "en", "masc": "en", "fem": "en", "neut": "ett"})

#: Leading words a deck front carries that the mined lemma never does (S3): ``ett hus``, ``att gå``.
SV_LEADING_WORDS: frozenset[str] = frozenset({"en", "ett", "att"})

#: The S10 default for Swedish: the shared Latin parts with the shared Nordic dash rule. No inline flags.
SV_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, NORDIC_DIALOGUE_DASH_PATTERN)
)

_NORMALIZE_MAP = str.maketrans({"\N{NO-BREAK SPACE}": " ", "\N{SOFT HYPHEN}": None})


def sv_normalize(text: str) -> str:
    """S5 for Swedish: NFC; NBSP -> space; soft hyphens (e-book hyphenation points) removed."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)
