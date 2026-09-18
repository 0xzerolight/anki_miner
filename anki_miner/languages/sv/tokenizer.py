"""Swedish tokenizer: ``sv_core_news_sm`` through the shared spaCy adapter, parser kept.

The parser supplies the ``compound:prt`` arc the particle stash reads; the parser-side join is
``SeparableVerbPass`` with ``swedish_particle_candidates`` (``sv/parser.py``). ``relemmatise_capitalised`` is on
(Ruling S2, variant R): the model's lemmatiser returns a capitalised inflected word as written (``Huset`` ->
``Huset``) and the casing rule only lowers that, where the same word lowercased lemmatises to ``hus``; on UD
Swedish Talbanken dev+test it lifts sentence-initial content lemmas from 73.3 % to 86.9 % exact and all content
lemmas from 91.52 % to 92.14 %, with gold PROPN mined as content unchanged. The same S8 abbreviation set the
splitter uses trims spaCy's dotted exceptions, so a sentence-final ``ung.`` or ``lat.`` is two tokens. No
``tag_char_map``: Swedish quotes with ``”`` and has no word-internal curly apostrophe. No ``join_hyphenated``: the
sv tokenizer has no letter-hyphen-letter infix, so ``e-postmeddelande`` already stays whole.
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.sv.abbreviations import SV_ABBREVIATIONS
from anki_miner.languages.sv.morphology import SV_MODEL_PACKAGE, SV_SEPARABLE_VERB_DEPS
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        SV_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=SV_SEPARABLE_VERB_DEPS,
        abbreviations=SV_ABBREVIATIONS,
        relemmatise_capitalised=True,
    )
