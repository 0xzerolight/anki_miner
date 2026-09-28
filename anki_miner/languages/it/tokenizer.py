"""Italian tokenizer: ``it_core_news_sm`` through the shared spaCy adapter.

Hyphen compounds stay whole (``italo-americano``; dictionaries key them whole
and the ladder falls back to the parts) while a glued dash still splits (the
shared dash infix). Curly apostrophes are folded in the tagging copy: the model
splits ``l’``/``dell’`` either way but tags ``po’``, ``Dov’`` and ``E’``
wrongly unless they read ``'``. A multi-word lemma keeps its first word
(``lavare si`` -> ``lavare``). The abbreviation set is the sentence
splitter's, so ``ecc.``/``prof.`` stay tokenizer exceptions and ``e.``/``a.`` do not.
No parser: Italian has no separable verbs.

Italian opts in to the shared capitalised-lemma repair (Ruling S2, variant R): its edit-tree lemmatiser
returns a capitalised inflected word unchanged (``Aspetta``, ``Pioveva``, ``Nuove``) - the first word of
most subtitle lines. UD Italian ISDT dev+test (1,046 sentences): content lemma exact 95.22 % -> 95.42 %,
sentence-initial 76.5 % -> 83.5 %, gold PROPN predicted as content unchanged (33 of 1,088). ``AUX``
joins the shared content set because the modals arrive as AUX (``Posso``, ``Vogliamo``) and the repair
runs before ``lift_modal_verbs`` makes them verbs; with it, gold AUX modal lemmas go 108 -> 109 of 111.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD, CAPITALISED_LEMMA_POS
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.it.morphology import (
    IT_ABBREVIATIONS,
    IT_MODEL_PACKAGE,
    keep_lemma_head,
    lift_modal_verbs,
)
from anki_miner.services.tagger import LockedTagger

#: The shared content set plus ``AUX``: the modals arrive as AUX, and the repair runs first.
IT_RELEMMATISE_POS: frozenset[str] = CAPITALISED_LEMMA_POS | {"AUX"}


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        IT_MODEL_PACKAGE,
        join_hyphenated=True,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(keep_lemma_head, lift_modal_verbs),
        abbreviations=IT_ABBREVIATIONS,
        relemmatise_capitalised=True,
        relemmatise_pos=IT_RELEMMATISE_POS,
    )
