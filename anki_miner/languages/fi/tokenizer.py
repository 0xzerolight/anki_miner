"""Finnish tokenizer: ``fi_core_news_sm`` through the shared spaCy adapter.

No parser: Finnish has no separable-particle join (E.1), and excluding the parser changes no lemma, POS, fine tag or
morph (0 of 3,000 example sentences differ). No hyphen join: spaCy's Finnish infixes never split an ASCII hyphen, so
``linja-auto`` is one token already. Curly apostrophes are folded in the tagging copy only: tagged as written, the
consonant-gradation apostrophe of ``ruo'an`` splits the word into ``ruo`` + the apostrophe + ``an``. The S8
abbreviation set is the sentence splitter's, so only those dotted exceptions stay glued (``esim.``, ``mm.``).
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.fi.morphology import FI_ABBREVIATIONS, FI_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(FI_MODEL_PACKAGE, tag_char_map=APOSTROPHE_FOLD, abbreviations=FI_ABBREVIATIONS)
