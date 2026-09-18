"""Hungarian data for the shared spaCy substrate (spec E.1 hu column, E.2.6, E.2.8, E.6).

Pinned on real ``hu_core_news_md`` 3.8.0 output (``tests/fixtures/hu/``) and wty-hu-en 2026.08.29 rows. Counts are
over the 7,701 example sentences of wty-hu-en (statistics only).

``HU_EXCLUDED_SUBTYPES`` is empty: the tagger's labels are the 17 UPOS names, so there is no fine tagset to exclude
by. The tagger and the morphologizer still predict independently, so ``pos2`` carries the tagger's UPOS guess
where the two disagree (1,075 of 28,546 content tokens, mostly NOUN tagged PROPN); nothing gates on it.

``HU_PREVERBS`` is every row of en.wiktionary's Appendix:Hungarian verbal prefixes (revision 92508665), variants and
debated or limited rows included. The parser labels a separated preverb ``compound:preverb`` (``nem olvasta el``,
``el fogom olvasni``): 465 such arcs, of which 429 have the VERB or AUX head ``PARTICLE_HEAD_POS`` requires, and 23
of those hang a word that is no preverb (``volna``, ``legalább``, ``hogy``, a closing quote).
``hungarian_preverb_candidates`` offers a join only for a listed preverb: without a dictionary ``SeparableVerbPass``
takes the first candidate, which would print ``volnatör``. The 36 arcs on a non-verb head (X, ADJ, PROPN, NOUN, ADV,
NUM) are skipped before the demotion, so their dependent mines as its own ADV (``nem érhető el`` mines ``el``), and a
head with two arcs keeps the first (``hitte volna el`` stashes ``volna``, so the front stays ``hisz``).

``preverb_less_verb`` is the lookup rung (E.2.8, the German rung's shape): wty-hu-en lacks many preverb verbs the
lemmatiser builds (``elkap``, ``elenged``, ``behoz``). It reads the front only, has no POS gate and emits every
listed preverb the front starts with (``előrefut`` -> ``fut``, ``refut``, ``őrefut``); over the corpus's 28,546
ADJ/ADV/NOUN/VERB tokens 310 reach the dictionary through it alone (VERB 242, NOUN 33, ADJ 31, ADV 4) against
24,977 direct front hits and 403 surface hits.

``demote_question_clitic``: spaCy's hu tokenizer splits the ``-e`` question clitic off (``tudod-e``), and the model
tags it ADV, which would mine.

``HU_OPENERS``/``HU_CLOSERS``: Hungarian quotes are „…” outside and »…« inside, so ``»`` opens and ``«`` closes, the
reverse of the shared Latin pair. ``HU_SPEAKER_PATTERN`` is the shared speaker label with ``Ő`` and ``Ű`` added:
both sit outside Latin-1, so ``GYŐZŐ:`` escaped the Latin default.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import BRACKETS_PATTERN, DIALOGUE_DASH_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages.token import LanguageToken

#: The model package the tokenizer loads and the availability probe looks for (a HuggingFace wheel, E.6).
HU_MODEL_PACKAGE = "hu_core_news_md"

HU_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
HU_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: UD Hungarian labels a separated verbal prefix ``compound:preverb`` (spec §4.3 item 2, E.10).
HU_PREVERB_DEPS: frozenset[str] = frozenset({"compound:preverb"})

HU_PREVERBS: frozenset[str] = frozenset(
    {
        "abba", "agyon", "alul", "alá", "alább", "be", "bele", "belé", "benn", "egybe", "egyet", "együtt", "el",
        "ellen", "ellent", "elé", "elő", "előre", "fel", "felül", "fenn", "félbe", "félre", "föl", "fölé", "fölül",
        "fönn", "haza", "helyben", "helyre", "helyt", "hozzá", "hátra", "ide", "jól", "jót", "jóvá", "karban",
        "keresztül", "ketté", "ki", "kétségbe", "kölcsön", "körbe", "köré", "körül", "közbe", "közben", "közre",
        "közzé", "közé", "külön", "le", "létre", "meg", "mellé", "mögé", "nagyot", "neki", "nyilván", "oda",
        "odább", "odébb", "ott", "rajta", "rendre", "rosszul", "rá", "szembe", "szemre", "szerte", "számon",
        "széjjel", "szét", "szörnyet", "tele", "teli", "tova", "tovább", "tönkre", "túl", "utol", "után", "utána",
        "vissza", "viszont", "végbe", "végig", "végre", "által", "át", "észre", "össze", "újjá", "újra"
    }
)  # fmt: skip

#: Longest first, so ``előre`` is stripped before ``elő`` and ``el``.
_PREVERBS_LONGEST_FIRST: tuple[str, ...] = tuple(sorted(HU_PREVERBS, key=lambda preverb: (-len(preverb), preverb)))

#: A stripped preverb must leave a verb behind: ``ad``, ``ír`` and ``lő`` have two letters.
_MIN_VERB_REST = 2


def hungarian_preverb_candidates(token: Any) -> list[str]:
    """``SeparableVerbPass`` candidates: preverb + lemma for a listed preverb, none for any other stashed word."""
    particle: str = token.feature.particle
    return [particle + token.feature.lemma] if particle in HU_PREVERBS else []


def preverb_less_verb(word: str, surface: str) -> list[str]:
    """Lookup rung over ``(mined_form, surface)``: ``elkap`` -> ``kap``, longest preverb first.

    Reads the card front only: a surface never carries a preverb the front lacks.
    """
    del surface
    return [
        word[len(preverb) :]
        for preverb in _PREVERBS_LONGEST_FIRST
        if word.startswith(preverb) and len(word) - len(preverb) >= _MIN_VERB_REST
    ]


def demote_question_clitic(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass (``build_spacy_tagger(post_passes=...)``): the split-off ``-e`` clitic becomes PART."""
    for token in tokens:
        if token.surface.casefold() == "-e":
            token.feature.pos1 = "PART"
    return tokens


#: Words a deck front carries that the mined lemma never does (S3, D18): ``a ház`` meets ``ház``.
HU_LEADING_WORDS: frozenset[str] = frozenset({"a", "az", "egy"})

HU_OPENERS: frozenset[str] = frozenset("([{“„»")
HU_CLOSERS: frozenset[str] = frozenset(")]}”«")

#: ``GYŐZŐ:``, ``ŐR:`` — the Latin speaker label with Ő (U+0150) and Ű (U+0170) in both classes.
HU_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞŐŰ][A-ZÀ-ÖØ-ÞŐŰ0-9 .'-]*[A-ZÀ-ÖØ-ÞŐŰ]:\s*"
HU_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, HU_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)
