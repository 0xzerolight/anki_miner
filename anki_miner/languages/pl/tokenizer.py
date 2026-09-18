"""Polish tokenizer: ``pl_core_news_sm`` through the shared spaCy adapter.

No parser: Polish has no separable verbs, and dropping it changes no token,
POS, lemma, tag or morph (probed over 30 lines). No hyphen join and no
apostrophe fold. The post-passes (``pl/morphology.py``) repair the agglutinated
verb lemmas and the pluralia-tantum gender convention. The abbreviation set is
the sentence splitter's: it prunes the model's single-dot word rules (there are
none to prune for Polish) and, as tokenizer special cases, keeps each dotted
abbreviation one token, which the shared rule then tags ``X``.
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
    tagger = build_spacy_tagger(PL_MODEL_PACKAGE, post_passes=PL_POST_PASSES, abbreviations=PL_ABBREVIATIONS)
    _keep_abbreviation_dots(tagger.nlp)  # after the rule pruning; LockedTagger delegates to the SpacyTagger
    return tagger
