"""English tokenizer: ``en_core_web_sm`` through the shared spaCy adapter.

Hyphen compounds stay whole (``well-known``, ``mother-in-law``: dictionaries key
them whole and the ladder falls back to the parts) and curly apostrophes are
folded in the tagging copy only, because the model's contraction exceptions are
ASCII (``I’m`` otherwise tags ``’m`` as a verb).
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.en.morphology import EN_ABBREVIATIONS, EN_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point; the parser is not needed (no separable verbs).

    ``abbreviations`` prunes the model's ``Mass.``/``Co.``-style exceptions so a
    sentence-final word keeps its own token; the set is the one the sentence
    splitter uses.
    """
    return build_spacy_tagger(
        EN_MODEL_PACKAGE, join_hyphenated=True, tag_char_map=APOSTROPHE_FOLD, abbreviations=EN_ABBREVIATIONS
    )
