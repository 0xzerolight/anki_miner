"""Catalan tokenizer: ``ca_core_news_sm`` through the shared spaCy adapter.

No parser (no separable verbs) and no hyphen join (the Catalan tokenizer has no
letter-hyphen-letter rule, so ``nord-est`` is one token already). Curly
apostrophes are folded in the tagging copy: the model tags ``’l`` as a noun
otherwise. The S8 abbreviation set prunes the model's ``set.`` exception. One
Catalan fix goes onto the built pipeline before the tagger is handed out: the
lemma correction in ``ca/morphology.py``.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.ca.morphology import CA_ABBREVIATIONS, CA_MODEL_PACKAGE, install_lemma_correction
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    tagger = build_spacy_tagger(CA_MODEL_PACKAGE, tag_char_map=APOSTROPHE_FOLD, abbreviations=CA_ABBREVIATIONS)
    install_lemma_correction(tagger.nlp)  # before any call: nothing has tagged with this pipeline yet
    return tagger
