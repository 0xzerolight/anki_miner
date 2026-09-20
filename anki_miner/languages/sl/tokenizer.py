"""Slovenian tokenizer: ``sl_core_news_sm`` through the shared spaCy adapter.

No dependency parser (Slovenian has no separable verbs, and dropping it changes no token, POS,
lemma, tag or morph), no hyphen join (spaCy Slovenian already keeps letter-hyphen-letter compounds
whole), no apostrophe fold and no post-pass.

Slovenian opts in to the shared capitalised-lemma repair (Ruling S2, variant R): the lemmatiser
returns a capitalised sentence-initial content word unchanged, which is the first word of most
subtitle lines. UD Slovenian-SSJ dev+test: content lemma exact 90.76 % -> 91.14 %, sentence-initial
950 -> 1,025 of 1,172, gold PROPN predicted as content unchanged at 145.

``SL_ABBREVIATIONS`` is the one source of truth for "this dotted word is an abbreviation": it is the
sentence splitter's set and it prunes every single-dot tokenizer rule whose stem is not in it
(spaCy ships 1,203 Slovenian exceptions, 178 of whose stems are ordinary words).
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.sl.abbreviations import SL_ABBREVIATIONS
from anki_miner.languages.sl.morphology import SL_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        SL_MODEL_PACKAGE,
        abbreviations=SL_ABBREVIATIONS,
        relemmatise_capitalised=True,
    )
