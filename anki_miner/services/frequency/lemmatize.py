"""In-app lemmatisation of surface-keyed frequency lists (S17).

A frequency list keyed on surface forms ranks an infinitive by its own
occurrences only. The importer sums every form under its lemma
(``source_importer._aggregate_by_lemma``); this module builds the term -> lemma
mapping from the mining language's own tagger, so no language branch lives
outside ``languages/``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

Lemmatizer = Callable[[list[str]], list[str]]


class LemmatizeKwarg(TypedDict, total=False):
    """The ``lemmatize=`` keyword an importer call is splatted with (S17).

    Absent when there is no lemmatizer, so such a call keeps its pre-S17 shape
    (the ``LanguageKwarg`` idiom).
    """

    lemmatize: Lemmatizer


def lemmatize_kwarg(lemmatize: Lemmatizer | None) -> LemmatizeKwarg:
    """``{"lemmatize": lemmatize}``, or nothing at all when there is none."""
    return {} if lemmatize is None else {"lemmatize": lemmatize}


def build_frequency_lemmatizer(language: str) -> Lemmatizer:
    """Return a lemmatizer over *language*'s tagger, resolved on first call.

    A term the tagger splits into more than one token (``don't``) is not a word
    the list can re-rank, so it keeps its own spelling. Built lazily: the tagger
    may be an engine that costs seconds to load, and the import runs off the GUI
    thread.
    """

    def lemmatize(words: list[str]) -> list[str]:
        from anki_miner.languages.tagger_provider import get_tagger
        from anki_miner.services.morphology import extract_lemma

        tagger = get_tagger(language)
        lemmas: list[str] = []
        for word in words:
            tokens = list(tagger(word))
            lemmas.append(extract_lemma(tokens[0]) if len(tokens) == 1 else word)
        return lemmas

    return lemmatize


def manual_import_lemmatizer(language: str) -> Lemmatizer | None:
    """The lemmatizer for a hand-added list, or None when the language does not declare one.

    Declared by the ``lemmatised_frequency`` capability: only a profile whose
    tagger lemmatises reliably opts a user's own list into aggregation.
    """
    from anki_miner.languages.registry import get_profile

    if "lemmatised_frequency" not in get_profile(language).capabilities:
        return None
    return build_frequency_lemmatizer(language)
