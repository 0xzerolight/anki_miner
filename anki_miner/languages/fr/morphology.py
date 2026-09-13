"""French tables and the French lemma repair.

Everything language-varying for French that is data lives here: the POS gate,
sentence abbreviations, known-word leading words, the gender labels, the
tokenizer's clitic/title/fixed-token tables, the key fold and ``normalize``, and
``FrenchVerbLemmaPass``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD

if TYPE_CHECKING:  # annotation-only: no services import at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

logger = logging.getLogger(__name__)

#: The model package the tokenizer loads and the availability probe looks for.
FR_MODEL_PACKAGE = "fr_core_news_sm"

#: S8 keys (casefolded, no final dot), fed to BOTH ``sentence_rules`` and
#: ``build_spacy_tagger`` (contract item 17: a spaCy single-dot exception whose
#: stem is not here is pruned, so its word keeps its own token). spaCy's French
#: dotted exceptions minus the words a sentence ends on — ``sept`` (seven),
#: ``vol`` (flight) — plus ``mgr``, ``me`` (Maître), ``c.-à-d`` and ``p``
#: (``p. ex.``; ``ex`` itself is a noun: "mon ex."). ``etc`` stays: its dot
#: then stays on the token, which is an ``X`` abbreviation, not a card.
FR_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles
        "m", "mm", "mme", "mlle", "dr", "mr", "me", "mgr", "st", "ste",
        # references
        "cf", "etc", "p", "pp", "no", "c.-à-d", "j.-c", "av", "apr",
        # months
        "janv", "févr", "avr", "juill", "oct", "nov", "déc",
    }
)  # fmt: skip

#: Pronouns written after a hyphen (inversion and imperatives): ``dit-elle``,
#: ``Donne-le-moi``, ``va-t'en``, ``cette fois-ci``. ``fr_core_news_sm``'s own
#: suffix list covers ``ce elle en il ils je là moi nous on t vous`` only; the
#: tokenizer splits every entry and the retag makes each a pronoun (the model
#: tags ``-elle`` ADJ lemma ``-ell``, ``-toi`` NOUN, ``-lui`` PROPN, ``-y`` PUNCT).
#: Apostrophe entries are ASCII: the tagging copy folds ``’`` first.
FR_CLITIC_PRONOUNS: tuple[str, ...] = (
    "le", "la", "les", "lui", "leur", "moi", "toi", "nous", "vous", "y", "en",
    "je", "tu", "il", "elle", "on", "ils", "elles", "ce", "t", "là", "ci",
    "t'en", "m'en", "t'y", "m'y", "l'y",
)  # fmt: skip

#: Titles written without their dot (dotted ``M.``/``Mme.`` are already ``X``,
#: contract item 7). The model tags ``Mme``/``Dr`` NOUN. Case-sensitive, with the
#: all-caps forms a shouted cue keeps (``MME DUPONT``), so ``m`` (the metre) stays a noun.
FR_TITLE_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "M",
        "MM",
        "Mme",
        "Mmes",
        "Mlle",
        "Mlles",
        "Dr",
        "Pr",
        "Me",
        "Mgr",
        "Mr",
        "MME",
        "MMES",
        "MLLE",
        "MLLES",
        "DR",
        "PR",
        "MGR",
    }
)

#: Multi-word adverbs kept as one token with their wty headword as the lemma:
#: split, ``c'est-à-dire`` mines ``est-à-dire`` VERB.
FR_FIXED_TOKENS: Mapping[str, tuple[str, str]] = MappingProxyType({"c'est-à-dire": ("ADV", "c'est-à-dire")})

#: Spellings kept whole by a tokenizer special case (and its sentence-initial
#: capital): ``c'est-à-dire``; ``c.-à-d.`` (no spaCy exception: it would lose its
#: dot and mine ``c.-à-d`` ADJ; whole, it is a dotted ``X``); and the five spaCy
#: hyphen exceptions ending in a clitic, which spaCy lists lowercase only
#: (``Rendez-vous`` would split into ``Rendez`` ``-vous``).
FR_WHOLE_WORDS: tuple[str, ...] = (
    "c'est-à-dire", "c.-à-d.", "celle-ci", "celles-ci", "celui-ci", "jusque-là", "rendez-vous",
)  # fmt: skip

_APOSTROPHE_TABLE = str.maketrans(dict(APOSTROPHE_FOLD))


def fold_apostrophes(text: str) -> str:
    """``’ ‘ ʼ ´`` → ``'``, one character for one: wty spells ``aujourd'hui`` with ASCII."""
    return text.translate(_APOSTROPHE_TABLE)


def _needs_infinitive(token: Any) -> bool:
    feature = token.feature
    lemma = str(getattr(feature, "lemma", "") or "")
    return bool(feature.pos1 == "VERB" and lemma.endswith("e") and lemma == token.surface.casefold())


class FrenchVerbLemmaPass:
    """A ``token_post_pass`` (Stage S seam) repairing the rule lemmatizer's ``-e`` gap.

    ``fr_core_news_sm``'s VERB rules carry ``es→er``, ``ons→er``, ``ent→er`` …
    but no ``e→er``, and its lookup table answers the NOUN for ``porte``,
    ``donne``, ``reste``, ``garde``, ``compte``, ``joue``, ``laisse``,
    ``marche``, ``montre`` — so ``il porte`` keeps ``porte`` as its lemma. For a
    VERB whose lemma ends in ``e`` and equals its casefolded surface, the
    candidate ``lemma + "r"`` replaces it only when the dictionary knows that
    headword (one attestation call per line). ``attest is None`` — no offline
    dictionary — changes nothing: without evidence the model's lemma stands. A
    repaired lemma no longer equals its surface, so a second run is a no-op.
    The third argument (R36's form lookup) is ignored.
    """

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del forms
        if attest is None:
            return tokens
        repairs = [token for token in tokens if _needs_infinitive(token)]
        if not repairs:
            return tokens
        candidates = [token.feature.lemma + "r" for token in repairs]
        attested = attest(list(dict.fromkeys(candidates)))
        for token, candidate in zip(repairs, candidates, strict=True):
            if candidate in attested:
                token.feature.lemma = candidate
            else:
                logger.debug("French infinitive %r not attested; keeping %r", candidate, token.feature.lemma)
        return tokens
