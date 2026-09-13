"""German tokenizer: ``de_core_news_sm`` through the shared spaCy adapter, parser kept.

The parser stays in the pipeline for the ``svp`` arc (separable particles, spec
§4.3 item 2). Hyphen compounds need no infix removal: spaCy's German punctuation
has no letter-hyphen-letter infix, so ``E-Mail`` is already one token. Nouns
keep their capital (``title_case_pos``); curly apostrophes are folded in the
tagging copy only, so the card keeps the subtitle's own text. The abbreviation
set is the one the sentence splitter uses, so only those dotted exceptions stay
glued (``z.B.``, ``Dr.``) and a sentence-final ``Jan.`` keeps its own token.

``ES_CLITIC_SUFFIX``: German dialogue glues ``'s`` (``es``) onto verbs —
``geht's``, ``gibt's``, ``hab's``. spaCy's German exceptions split it only after
a pronoun (``du's``), so a verb kept it (lemma ``geht's``, a junk card front)
and a curly ``geht’s`` split off a ``’s`` tagged NOUN. With the suffix and the
folded copy, ``'s`` is PRON/PPER with lemma ``es``.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.de.morphology import (
    DE_ABBREVIATIONS,
    DE_MODEL_PACKAGE,
    SEPARABLE_VERB_DEPS,
    adjd_as_adjective,
)
from anki_miner.services.tagger import LockedTagger

ES_CLITIC_SUFFIX = r"(?<=[^\W\d_])'[sS]"


def _split_es_clitic(nlp: Any) -> None:
    from spacy.util import compile_suffix_regex

    nlp.tokenizer.suffix_search = compile_suffix_regex([*nlp.Defaults.suffixes, ES_CLITIC_SUFFIX]).search


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    tagger = build_spacy_tagger(
        DE_MODEL_PACKAGE,
        keep_parser=True,
        title_case_pos=frozenset({"NOUN"}),
        particle_deps=SEPARABLE_VERB_DEPS,
        tag_char_map=APOSTROPHE_FOLD,
        post_passes=(adjd_as_adjective,),
        abbreviations=DE_ABBREVIATIONS,
    )
    _split_es_clitic(tagger.nlp)  # LockedTagger delegates attribute access to the SpacyTagger
    return tagger
