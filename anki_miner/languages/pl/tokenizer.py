"""Polish tokenizer: ``pl_core_news_sm`` through the shared spaCy adapter.

No parser: Polish has no separable verbs, and dropping it changes no token,
POS, lemma, tag or morph (probed over 30 lines). No hyphen join and no
apostrophe fold. The post-passes (``pl/morphology.py``) repair the agglutinated
verb lemmas and the pluralia-tantum gender convention. The abbreviation set is
the sentence splitter's: it prunes the model's single-dot word rules (there are
none to prune for Polish) and, as tokenizer special cases, keeps each dotted
abbreviation one token, which the shared rule then tags ``X``. Variant R
(``relemmatise_capitalised``) repairs the line-initial verbs the model
lemmatises to themselves; ``Mógłbyś`` stays a residual miss.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.pl.morphology import (
    PL_ABBREVIATIONS,
    PL_MODEL_PACKAGE,
    PL_POST_PASSES,
    abbreviation_cases,
)
from anki_miner.services.tagger import LockedTagger


def _keep_abbreviation_dots(nlp: Any) -> None:
    from spacy.symbols import ORTH

    for spelling in abbreviation_cases(PL_ABBREVIATIONS):
        nlp.tokenizer.add_special_case(spelling, [{ORTH: spelling}])


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    tagger = build_spacy_tagger(
        PL_MODEL_PACKAGE,
        post_passes=PL_POST_PASSES,
        abbreviations=PL_ABBREVIATIONS,
        # S2 FINAL variant R: a capitalised content word whose RAW lemma is its own surface is
        # re-lemmatised lowercased (Boję -> bać, Otwórz -> otworzyć). Measured on UD Polish PDB
        # r2.8 dev+test: content lemma exactness 89.49% -> 90.37%, names mined as content
        # unchanged at 234 of 2,277 (plan P9).
        relemmatise_capitalised=True,
    )
    _keep_abbreviation_dots(tagger.nlp)  # after the rule pruning; LockedTagger delegates to the SpacyTagger
    return tagger
