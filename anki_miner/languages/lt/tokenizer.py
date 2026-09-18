"""Lithuanian tokenizer: ``lt_core_news_sm`` through the shared spaCy adapter.

No parser: Lithuanian has no separable particles (``labels.parser`` carries no ``compound`` label, E.1), and the
model's own sentence boundaries (``sents_f`` .79) are never consulted — books split with the profile's
``SentenceRules``. The letter-hyphen-letter split stays (Lithuanian writes compounds closed; a hyphen joins ranges
and names, ``Vilniaus-Kauno``). No apostrophe fold: Lithuanian has no elision.

spaCy's lt tokenizer exceptions drop every dotted abbreviation, so ``m.`` splits into ``m`` + ``.`` and the model
tags the ``m`` a NOUN with lemma ``m.``, a card front. Each ``LT_ABBREVIATIONS`` key goes back in as a special
case in its lower, capitalised and upper spelling (the all-caps copy is lowercase): ``m.``, ``Pvz.``, ``A.``,
``t.t.`` stay one token, which the shared adapter tags ``X``. The same set is ``build_spacy_tagger``'s
``abbreviations`` (contract item 17).
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.lt.morphology import LT_ABBREVIATIONS, LT_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger


def abbreviation_spellings(keys: frozenset[str]) -> list[str]:
    """``pvz`` → ``pvz.``, ``Pvz.``, ``PVZ.``: every dotted spelling a special case covers, sorted and distinct."""
    return sorted({variant + "." for key in keys for variant in (key, key[:1].upper() + key[1:], key.upper())})


def _add_abbreviation_cases(nlp: Any, spellings: list[str]) -> None:
    from spacy.symbols import ORTH

    for spelling in spellings:
        nlp.tokenizer.add_special_case(spelling, [{ORTH: spelling}])


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    tagger = build_spacy_tagger(LT_MODEL_PACKAGE, abbreviations=LT_ABBREVIATIONS)
    _add_abbreviation_cases(tagger.nlp, abbreviation_spellings(LT_ABBREVIATIONS))  # before any call
    return tagger
