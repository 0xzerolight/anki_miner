"""Hungarian tokenizer: ``hu_core_news_md`` (HuSpaCy) through the shared spaCy adapter, parser kept.

The model package registers its two lemmatiser factories (``hu.lookup_lemmatizer``, ``trainable_lemmatizer_v2``)
when it is imported, and the shared loader imports the package before calling its ``load`` (E.6, D12). The parser
supplies the ``compound:preverb`` arc the preverb stash reads (``nem olvasta el``); the parser-side join is
``SeparableVerbPass`` with ``hungarian_preverb_candidates`` (``hu/parser.py``). ``demote_question_clitic`` keeps
the ``-e`` clitic out of mining. hu's tokenizer has no letter-hyphen-letter infix, so ``észak-amerikai`` stays one
token. The S8 abbreviation set trims spaCy's dotted exceptions, so a sentence-final ``be.`` or ``út.`` is two tokens.
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.hu.abbreviations import HU_ABBREVIATIONS
from anki_miner.languages.hu.morphology import HU_MODEL_PACKAGE, HU_PREVERB_DEPS, demote_question_clitic
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        HU_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=HU_PREVERB_DEPS,
        post_passes=(demote_question_clitic,),
        abbreviations=HU_ABBREVIATIONS,
    )
