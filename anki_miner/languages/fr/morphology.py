"""French tables and the French lemma repair.

Everything language-varying for French that is data lives here: the POS gate,
sentence abbreviations, known-word leading words, the gender labels, the
tokenizer's clitic/title/fixed-token tables, the key fold and ``normalize``, and
``FrenchVerbLemmaPass``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotation-only: no services import at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

logger = logging.getLogger(__name__)


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
