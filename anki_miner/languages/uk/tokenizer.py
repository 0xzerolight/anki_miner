"""Ukrainian tokenizer: ``uk_core_news_sm`` through the shared spaCy adapter (plan P3, P5, P6, P6a).

No parser: mining reads no dependency arc. The model tags a copy whose apostrophes are all U+0027 -
the only spelling ``pymorphy3-dicts-uk`` and ``wty-uk-en`` know - and the letter-hyphen-letter split
is dropped so по-українськи and інтернет-магазин stay whole instead of mining a half-word.
``PymorphyLemmaRepair`` then fixes the joined tokens (the model tags them at random: по-українськи
as a feminine NOUN) and the identity lemmas, asking the analyser for the canonical spelling in both
branches. ``spacy`` is imported only here, at build time: a profile builds on a machine without the
engine, where ``tagger_provider`` turns the ImportError into its handled ValueError.
"""

from __future__ import annotations

from anki_miner.languages._spaced.pymorphy import PymorphyLemmaRepair
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.uk.abbreviations import UK_ABBREVIATIONS
from anki_miner.languages.uk.morphology import (
    UK_ALLOWED_POS,
    UK_MODEL_PACKAGE,
    UK_TAG_CHAR_MAP,
    apostrophe_blind,
    canonical_apostrophes,
)
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    # UkrainianLemmatizer subclasses RussianLemmatizer and pymorphy3-dicts-uk uses the same
    # OpenCorpora tagset, so oc2ud is the right OpenCorpora -> UD map for both languages.
    from spacy.lang.ru.lemmatizer import oc2ud

    # analysis_form is load-bearing, not decoration (plan P6a): pymorphy3-dicts-uk knows only
    # U+0027, so a typographic surface gets is_known=False on every parse and the repair would
    # write back the inflected form it exists to fix.
    repair = PymorphyLemmaRepair(allowed_pos=UK_ALLOWED_POS, fold=apostrophe_blind, analysis_form=canonical_apostrophes)
    tagger = build_spacy_tagger(
        UK_MODEL_PACKAGE,
        join_hyphenated=True,
        tag_char_map=UK_TAG_CHAR_MAP,
        post_passes=(repair,),
        abbreviations=UK_ABBREVIATIONS,
    )
    # spaCy's UkrainianLemmatizer owns the one MorphAnalyzer (it builds it itself); a second would
    # load the 14 MB dictionaries twice. LockedTagger delegates ``.nlp`` to the SpacyTagger.
    repair.bind(tagger.nlp.get_pipe("lemmatizer")._morph, oc2ud)
    return tagger
