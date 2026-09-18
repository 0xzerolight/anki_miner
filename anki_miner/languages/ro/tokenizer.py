"""Romanian tokenizer: ``ro_core_news_sm`` through the shared spaCy adapter.

No parser (no separable verbs) and no hyphen join (spaCy Romanian has no letter-hyphen-letter infix; clitics
such as ``s-a`` split by prefix stripping). The model tags a comma-below copy of the line
(``RO_CEDILLA_FOLD``), the main-verb post-pass lifts ``Vm`` tags out of AUX, and the S8 abbreviation set
prunes the ``rom.``/``ian.``/``ex.`` exceptions.

Romanian opts in to the shared capitalised-lemma repair (Ruling S2, variant R): its edit-tree lemmatiser
returns a capitalised inflected word unchanged (``Cărțile``, ``Mergem``) - the first word of most subtitle
lines. UD Romanian RRT v2.8 dev+test: content lemma exact 91.50 % -> 92.06 %, sentence-initial 75.6 % ->
83.7 %, gold PROPN predicted as content unchanged (87 of 966). ``AUX`` joins the shared content set because
the attribute ruler files 11 main-verb tags as AUX (``Vreau``, ``Știu``) and the repair runs before
``main_verb_pos`` lifts them back.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import CAPITALISED_LEMMA_POS
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.ro.morphology import (
    RO_ABBREVIATIONS,
    RO_CEDILLA_FOLD,
    RO_MODEL_PACKAGE,
    main_verb_pos,
)
from anki_miner.services.tagger import LockedTagger

#: The shared content set plus ``AUX``: ro's main verbs arrive as AUX (R2), and the repair runs first.
RO_RELEMMATISE_POS: frozenset[str] = CAPITALISED_LEMMA_POS | {"AUX"}


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        RO_MODEL_PACKAGE,
        tag_char_map=RO_CEDILLA_FOLD,
        post_passes=(main_verb_pos,),
        abbreviations=RO_ABBREVIATIONS,
        relemmatise_capitalised=True,
        relemmatise_pos=RO_RELEMMATISE_POS,
    )
