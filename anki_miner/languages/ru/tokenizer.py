"""Russian tokenizer: ``ru_core_news_sm`` through the shared spaCy adapter (plan D7).

No parser: dropping it changes no token, POS, lemma or morph (probed over 23 lines). The model tags a
copy with ё -> е (it saw almost no ё in training); the letter-hyphen-letter split is dropped so
кто-то and по-русски stay whole; the abbreviation set prunes spaCy's rules that glue a real word to a
final dot. ``RuLemmaRepair`` then fixes hyphenated tokens and identity lemmas with the lemmatizer's
own pymorphy3 analyser. ``spacy`` is imported only here, at build time: a profile builds on a machine
without the engine.
"""

from __future__ import annotations

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.ru.abbreviations import RU_ABBREVIATIONS
from anki_miner.languages.ru.morphology import RU_MODEL_PACKAGE, RU_TAG_CHAR_MAP, RuLemmaRepair
from anki_miner.services.tagger import LockedTagger


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    from spacy.lang.ru.lemmatizer import oc2ud

    repair = RuLemmaRepair()
    tagger = build_spacy_tagger(
        RU_MODEL_PACKAGE,
        join_hyphenated=True,
        tag_char_map=RU_TAG_CHAR_MAP,
        post_passes=(repair,),
        abbreviations=RU_ABBREVIATIONS,
    )
    # spaCy's RussianLemmatizer owns the one MorphAnalyzer (it builds it itself); a second would load
    # the 8 MB dictionaries twice. LockedTagger delegates ``.nlp`` to the SpacyTagger.
    repair.bind(tagger.nlp.get_pipe("lemmatizer")._morph, oc2ud)
    return tagger
