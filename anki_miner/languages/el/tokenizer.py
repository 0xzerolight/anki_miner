"""Greek tokenizer: ``el_core_news_sm`` through the shared spaCy adapter.

No parser (Greek has no separable verbs; ``labels.parser`` has no ``compound:prt``) and no hyphen
join (``ελληνο-τουρκική`` is already one token; glued dashes still split through the shared dash
infix). Curly and modifier apostrophes are folded in the tagging copy: the model's elision exceptions
know ``'`` and ``’`` but not U+02BC, so ``Θ\u02bc`` would tag ADJ and mine as a word. The abbreviation set
is the sentence splitter's, so ``κ.``/``χλμ.`` stay tokenizer exceptions and ``Νικ.``/``αν.`` do not.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.el.morphology import EL_ABBREVIATIONS, EL_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(EL_MODEL_PACKAGE, tag_char_map=APOSTROPHE_FOLD, abbreviations=EL_ABBREVIATIONS)
