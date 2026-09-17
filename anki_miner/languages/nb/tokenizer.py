"""Norwegian Bokmål tokenizer: ``nb_core_news_sm`` through the shared spaCy adapter, parser kept.

The parser supplies the ``compound:prt`` arc the particle stash reads; the parser-side join is
``SeparableVerbPass`` with ``norwegian_particle_candidates`` (``nb/parser.py``). The same S8 abbreviation set the
splitter uses trims spaCy's dotted exceptions, so a sentence-final ``ti.``, ``min.`` or ``jul.`` is two tokens. No
apostrophe fold: tagged as written, ``Lars’ bil`` is PROPN + X; folded to ``Lars'`` it would be one ADJ token. nb's
infixes keep hyphen compounds (``e-posten``) whole already.

``relemmatise_capitalised`` opts into RULING S2 FINAL's variant R: a sentence-initial capitalised content word
whose lemma is its own surface is re-lemmatised lowercased, POS kept (``Studenten`` → ``student``). Measured
on UD Norwegian Bokmaal dev+test: all-content lemma accuracy 94.11 % → 94.42 %, and PROPN-anywhere predicted
content stays at 155/3,944 — the ruling's opt-in bar.
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.nb.abbreviations import NB_ABBREVIATIONS
from anki_miner.languages.nb.morphology import NB_MODEL_PACKAGE, NB_SEPARABLE_VERB_DEPS
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        NB_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=NB_SEPARABLE_VERB_DEPS,
        abbreviations=NB_ABBREVIATIONS,
        relemmatise_capitalised=True,
    )
