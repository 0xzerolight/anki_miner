"""Lithuanian SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

wty-lt-en keys every lemma row unstressed but writes many form-row targets with stress marks, often
beside the plain spelling (``paliko`` names ``palikti`` and ``pali̇̀kti``), so the targets are read
through the same stress fold as the keys (plan D-1): the two collapse to one and the card front
carries no marks.

A negated verb fronts its positive verb, the way Lithuanian dictionaries (DLKŽ) file it. ``lt_core_news_sm``
marks it ``Polarity=Neg`` and keeps the ne- in the lemma (``Negaliu`` -> ``negalėti``) or leaves the surface
(``nepasakei``). wty-lt-en has a lemma row for five negated verbs, each only "negative form of X"
(``negalėti``, ``nežinoti``, ``nebūti``, ``nenorėti``, ``nereikėti``), and no row for any other, so the card
duplicated a verb the learner already had or was never made. Two dictionary-gated steps, so a parser without a
dictionary keeps the ne- front:

* ``NegatedVerbPass`` runs first and strips the ne- from a lemma whose every lemma row is such a "negative form of
  <the lemma without ne->" row: the form-of repair then keeps the positive verb, however the surface's own form row
  points back at the ne- lemma (``negali`` names ``negalėti``).
* ``negated_verb_candidates`` gives the form-of repair the lemma and the surface without ne- to read after the
  surface (``neateis``, lemmatised ``neateisti``, no row -> ``ateis`` names ``ateiti``). The repair reads them only
  for a lemma with no lemma row, so a ne- verb with a sense of its own (``nekęsti`` to hate, not ``kęsti`` to
  endure) is never stripped. A negated verb takes only a front whose lemma row is a verb (``negated_front_is_verb``):
  ``Nebėra`` (lemmatised ``nebebūti``, no row) would otherwise read ``bėra`` and front the adjective ``bėras``.

The it-style attested-lemma pass was measured and left out: on ALKSNIS dev+test it fires on 426 of
11,987 content tokens for a net +17 correct lemmas (plan 2026-09-17-lt, D-7).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.form_of import (
    GLOSS_ITEM_RE,
    WTY_TAG_TO_UPOS,
    is_lemma_row,
    lemma_row_targets,
    rendered_text,
)
from anki_miner.languages.lt.morphology import strip_stress_marks

if TYPE_CHECKING:  # annotation-only: services must not load at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

_NE = "ne"
_NEGATED_POS = frozenset({"VERB", "AUX"})
_NEGATIVE_FORM_RE = re.compile(r"negative form of ([^\s;,.]+)")


def unstressed_row_targets(content: str, tags: str) -> list[str] | None:
    """``lemma_row_targets`` with the stress marks off every target."""
    targets = lemma_row_targets(content, tags)
    return None if targets is None else [strip_stress_marks(target) for target in targets]


def _negated(token: Any) -> bool:
    return token.feature.pos1 in _NEGATED_POS and "Polarity=Neg" in token.morph.split("|")


def _negative_form_of(content: str) -> str | None:
    """The verb a lemma row's first gloss calls it the "negative form of", or ``None``."""
    first = GLOSS_ITEM_RE.search(content or "")
    found = _NEGATIVE_FORM_RE.match(rendered_text(first.group(1))) if first else None
    return strip_stress_marks(found.group(1)) if found else None


def negated_verb_candidates(token: Any) -> list[str]:
    """``FormOfLemmaPass`` extra candidates: a negated verb's lemma, then its lowered surface, without the ne-."""
    if not _negated(token):
        return []
    words = (token.feature.lemma, token.surface.lower())
    return [word[len(_NE) :] for word in words if word.startswith(_NE) and len(word) > len(_NE)]


def negated_front_is_verb(token: Any, front: str, heads: Sequence[tuple[str, str]]) -> bool:
    """``FormOfLemmaPass`` accept gate: a negated verb's new front must open with a verb lemma row."""
    del front
    return not _negated(token) or WTY_TAG_TO_UPOS.get(heads[0][1].split(" ")[0]) == "VERB"


class NegatedVerbPass:
    """``token_post_pass``: a negated verb whose lemma rows are all "negative form of" its rest takes that rest."""

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest
        if forms is None:
            return tokens
        negated = [token for token in tokens if _negated(token) and token.feature.lemma.startswith(_NE)]
        if not negated:
            return tokens
        rows = forms(list(dict.fromkeys(token.feature.lemma for token in negated)))
        for token in negated:
            lemma = token.feature.lemma
            heads = [content for content, tags in rows.get(lemma, ()) if is_lemma_row(tags)]
            if heads and all(_negative_form_of(content) == lemma[len(_NE) :] for content in heads):
                token.feature.lemma = lemma[len(_NE) :]
        return tokens


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(
            NegatedVerbPass(),
            FormOfLemmaPass(
                row_targets=unstressed_row_targets,
                accept=negated_front_is_verb,
                extra_candidates=negated_verb_candidates,
            ),
        ),
    )
    return create_spaced_parser(config, **kwargs)
