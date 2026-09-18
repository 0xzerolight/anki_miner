"""Croatian tokenizer: ``hr_core_news_sm`` through the shared spaCy adapter.

No dependency parser (the model's ``labels.parser`` has ``compound`` but no ``compound:prt``, so there are no
separable verbs to reattach) and no hyphen join (spaCy Croatian keeps letter-hyphen-letter compounds whole).
The tagger reads an apostrophe-folded copy of the line: with a curly apostrophe ``Ko\u2019`` tags as an
abbreviation residual (``Y``) and with a straight one as a common noun, and the shared fold makes both
identical.

Croatian opts in to the shared capitalised-lemma repair (Ruling S2, variant R): its lemmatiser returns a
capitalised sentence-initial content word unchanged (``Morat``, ``\u010citat``), which is the first word of
most subtitle lines. UD Croatian-SET dev+test: content lemma exact 96.84 % -> 97.42 %, sentence-initial
1,072 -> 1,157 of 1,256, gold PROPN predicted as content unchanged at 31 of 2,879.

The dictionary-gated short-infinitive repair is the parser's ``token_post_pass``, not a tagger pass: it needs
an attestation lookup the tagger does not have.
"""

from __future__ import annotations

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.hr.morphology import HR_ABBREVIATIONS, HR_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        HR_MODEL_PACKAGE,
        tag_char_map=APOSTROPHE_FOLD,
        abbreviations=HR_ABBREVIATIONS,
        relemmatise_capitalised=True,
    )
