"""Dutch data for the shared spaCy substrate.

Pinned on real ``nl_core_news_sm`` 3.8.0 output (``tests/fixtures/nl/``) and wty-nl-en 2026.08.29 rows.

``NL_EXCLUDED_SUBTYPES``: ordinals carry UPOS ADJ but lemmatise to the cardinal (``tweede`` → ``twee``,
``eerste`` → ``één``, ``derde`` → ``drie``), a numeral front. Nothing else the corpus puts under
ADJ/ADV/NOUN/VERB is a non-word; ``er`` (``VNW|…adv-pron…``, ADV) stays mineable, because an excluded subtype
removes a word with no trace (the zh ruling).

``dutch_particle_candidates``: the model was trained on Alpino lemmas, which join a separable particle to its
verb, so with a particle in context the edit-tree lemmatiser often joins one itself — sometimes the wrong one
(``bel … op`` → ``terugbellen``, ``mee te gaan`` → ``overgaan``). Over 56 real arcs (``separable_verbs.jsonl``)
this order puts the right verb on 45 cards with a dictionary and 46 without, where particle + lemma alone manages
38 and 36 and prints a different verb seven times.

``restore_compound_hyphens``: the lemmatiser drops a compound's hyphen (``auto-ongeluk`` → ``autoongeluk``);
dictionaries key the hyphenated spelling.

``NL_ARTICLE_MAP`` / ``NL_GRAMMAR_SOURCES``: wty tags Dutch nouns masc/fem/neut and the model Com/Neut; every
non-neuter takes ``de``. The dictionary leads because morph describes the token, not the card front: the
diminutive ``hondje`` lemmatises to ``hond`` with ``Gender=Neut``, and ``hond``'s entry carries an obsolete
neuter row beside ``hond m``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import nfc_normalize
from anki_miner.languages.token import LanguageToken

#: The model package the tokenizer loads and the availability probe looks for.
NL_MODEL_PACKAGE = "nl_core_news_sm"

NL_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
NL_EXCLUDED_SUBTYPES: tuple[str, ...] = ("TW|rang|nom|mv-n", "TW|rang|nom|zonder-n", "TW|rang|prenom|stan")

#: UD Dutch labels a separable particle ``compound:prt`` (spec §4.3 item 2).
NL_SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"compound:prt"})

#: Particles a Dutch separable verb takes, longest first (so ``vooruit`` is found before ``voor``).
NL_SEPARABLE_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            "aan", "achter", "achteraan", "achterna", "achteruit", "af", "bij", "binnen", "boven", "deel",
            "dicht", "door", "heen", "in", "kapot", "klaar", "langs", "los", "mee", "mis", "na", "neer", "om",
            "onder", "op", "open", "over", "plaats", "rond", "samen", "schoon", "stil", "tegen", "terug",
            "thuis", "toe", "uit", "vast", "voor", "voorbij", "vooruit", "vrij", "weg",
        },
        key=lambda prefix: (-len(prefix), prefix),
    )
)  # fmt: skip

#: A stripped prefix must leave a verb behind: ``bijten`` is not ``bij`` + ``ten``, but ``zien`` is a verb.
_MIN_VERB_REST = 4


def dutch_particle_candidates(token: Any) -> list[str]:
    """Join candidates for a verb head carrying ``feature.particle``, best first (``SeparableVerbPass``).

    1. the lemma itself when it already starts with the particle (``op te bellen`` → ``opbellen``);
    2. particle + surface when the head is an infinitive, whose surface is its lemma
       (``mee te gaan``: model lemma ``overgaan``, candidate ``meegaan``);
    3. particle + lemma minus a different leading particle the model joined, when at least four letters
       remain (``bel … op``: ``terugbellen`` → ``opbellen``);
    4. particle + lemma.
    """
    particle: str = token.feature.particle
    lemma: str = token.feature.lemma
    out: list[str] = []
    if lemma.startswith(particle) and len(lemma) > len(particle):
        out.append(lemma)
    if "VerbForm=Inf" in str(getattr(token, "morph", "") or "").split("|"):
        out.append(particle + token.surface.casefold())
    joined = next((prefix for prefix in NL_SEPARABLE_PREFIXES if prefix != particle and lemma.startswith(prefix)), None)
    if joined is not None and len(lemma) - len(joined) >= _MIN_VERB_REST:
        out.append(particle + lemma[len(joined) :])
    out.append(particle + lemma)
    return list(dict.fromkeys(out))


def restore_hyphens(surface: str, lemma: str) -> str:
    """Put a compound's hyphens back where the lemmatiser dropped them (``auto-ongeluk``).

    Only when the surface has hyphens, the lemma has none, and each surface part before the last hyphen still
    begins the lemma (compared case-insensitively); otherwise the lemma is returned unchanged.
    """
    if "-" not in surface or "-" in lemma:
        return lemma
    parts = surface.split("-")
    if any(not part for part in parts):
        return lemma
    pieces: list[str] = []
    cursor = 0
    for part in parts[:-1]:
        piece = lemma[cursor : cursor + len(part)]
        if piece.lower() != part.lower():
            return lemma
        pieces.append(piece)
        cursor += len(part)
    tail = lemma[cursor:]
    return "-".join([*pieces, tail]) if tail else lemma


def restore_compound_hyphens(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass (``build_spacy_tagger(post_passes=...)``): ``restore_hyphens`` on every lemma."""
    for token in tokens:
        token.feature.lemma = restore_hyphens(token.surface, token.feature.lemma)
    return tokens


#: Leading words a deck front carries that the mined lemma never does (S3): ``het boek`` meets ``boek``.
NL_LEADING_WORDS: frozenset[str] = frozenset({"de", "het", "een", "'t", "’t", "zich"})

NL_ARTICLE_MAP: Mapping[str, str] = MappingProxyType({"masc": "de", "fem": "de", "common": "de", "neut": "het"})
NL_GRAMMAR_SOURCES: tuple[str, ...] = ("chips", "head", "morph")

#: ``„`` opens Dutch dialogue (``”`` already closes). ``‘ ’`` never move depth: ``’`` is also the apostrophe.
NL_EXTRA_OPENERS: frozenset[str] = frozenset("„")

_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None, "\u0132": "IJ", "\u0133": "ij"})


def nl_normalize(text: str) -> str:
    """S5 for Dutch: NFC; NBSP → space; soft hyphens (e-book hyphenation points) removed; ``Ĳ``/``ĳ`` → ``IJ``/``ij``."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)
