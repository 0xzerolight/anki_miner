"""Catalan tokenizer: ``ca_core_news_sm`` through the shared spaCy adapter.

No parser (no separable verbs) and no hyphen join (the Catalan tokenizer has no
letter-hyphen-letter rule, so ``nord-est`` is one token already). Curly
apostrophes are folded in the tagging copy: the model tags ``’l`` as a noun
otherwise. The S8 abbreviation set prunes the model's ``set.`` exception. Two
Catalan fixes go onto the built pipeline before the tagger is handed out, both in
``ca/morphology.py``: the lemma correction, and the enclitic-host rule that
retags ``Dona'm``'s host VERB before the lemmatizer reads it.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.ca.morphology import (
    CA_ABBREVIATIONS,
    CA_MODEL_PACKAGE,
    install_enclitic_host_rule,
    install_lemma_correction,
)
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    tagger = build_spacy_tagger(CA_MODEL_PACKAGE, tag_char_map=APOSTROPHE_FOLD, abbreviations=CA_ABBREVIATIONS)
    # Before any call: nothing has tagged with this pipeline yet.
    install_lemma_correction(tagger.nlp)
    install_enclitic_host_rule(tagger.nlp)
    return tagger
