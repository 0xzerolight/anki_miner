"""Danish tokenizer: ``da_core_news_sm`` through the shared spaCy adapter, parser kept.

The parser supplies the ``compound:prt`` arc the stash reads; the parser-side join is ``SeparableVerbPass`` with
``danish_particle_candidates`` (``da/parser.py``). The model keeps the surface as the lemma of a capitalised word
opening a sentence (``Huset``, ``B\u00f8rnene``), so Danish opts into the shared ``relemmatise_capitalised`` (Ruling
S2, variant R): UD Danish DDT dev+test content lemma exact 89.54 -> 90.17 %, gold PROPN predicted as content
unchanged at 77 of 1,035. The S8 abbreviation set trims spaCy's dotted exceptions, so a sentence-final ``man.`` or
``tv.`` is two tokens. Hyphen compounds (``tv-serie``) are one token already.
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.da.abbreviations import DA_ABBREVIATIONS
from anki_miner.languages.da.morphology import DA_MODEL_PACKAGE, DA_SEPARABLE_VERB_DEPS
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        DA_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=DA_SEPARABLE_VERB_DEPS,
        relemmatise_capitalised=True,
        abbreviations=DA_ABBREVIATIONS,
    )
