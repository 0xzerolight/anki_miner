"""Italian tokenizer: ``it_core_news_sm`` through the shared spaCy adapter.

Hyphen compounds stay whole (``italo-americano``; dictionaries key them whole
and the ladder falls back to the parts) while a glued dash still splits (the
shared dash infix). Curly apostrophes are folded in the tagging copy: the model
splits ``l’``/``dell’`` either way but tags ``po’``, ``Dov’`` and ``E’``
wrongly unless they read ``'``. A multi-word lemma keeps its first word
(``lavare si`` -> ``lavare``). The abbreviation set is the sentence
splitter's, so ``ecc.``/``prof.`` stay tokenizer exceptions and ``e.``/``a.`` do not.
No parser: Italian has no separable verbs.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.it.morphology import IT_ABBREVIATIONS, IT_MODEL_PACKAGE, keep_lemma_head
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        IT_MODEL_PACKAGE,
        join_hyphenated=True,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(keep_lemma_head,),
        abbreviations=IT_ABBREVIATIONS,
    )
