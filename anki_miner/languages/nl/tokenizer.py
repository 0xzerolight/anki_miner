"""Dutch tokenizer: ``nl_core_news_sm`` through the shared spaCy adapter, parser kept.

The parser supplies the ``compound:prt`` arc the separable-verb stash reads; the
parser-side join is ``SeparableVerbPass`` with ``dutch_particle_candidates``
(``nl/parser.py``). Curly apostrophes are folded in the tagging copy only: tagged
as written, ``’t`` is a NOUN that would mine. The model's lemmatiser drops a
compound's hyphen (``auto-ongeluk`` → ``autoongeluk``), so the tokenizer
post-pass ``restore_compound_hyphens`` repairs every lemma, where the frequency
lemmatizer, count_lemmas and Card Backfill see it too. The same S8
abbreviation set the splitter uses trims spaCy's dotted exceptions, so a
sentence-final ``hand.`` or ``kon.`` is two tokens. nl's tokenizer has no
letter-hyphen-letter infix, so hyphen compounds already stay whole.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.nl.abbreviations import NL_ABBREVIATIONS
from anki_miner.languages.nl.morphology import NL_MODEL_PACKAGE, NL_SEPARABLE_VERB_DEPS, restore_compound_hyphens
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        NL_MODEL_PACKAGE,
        keep_parser=True,
        particle_deps=NL_SEPARABLE_VERB_DEPS,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(restore_compound_hyphens,),
        abbreviations=NL_ABBREVIATIONS,
    )
