"""In-app lemmatisation of surface-keyed frequency lists (S17).

A frequency list keyed on surface forms ranks an infinitive by its own
occurrences only. The importer sums every form under its lemma
(``source_importer._aggregate_by_lemma``); this module builds the term -> lemma
mapping from the mining language's own tagger, so no language branch lives
outside ``languages/``.

The rank key must be the card front (SHARED-06). Tagged alone, a word can come
back with a lemma no dictionary knows (de ``welt`` -> ``Weln``, it ``ragazza``
-> ``ragazzare``). The parser's ``token_post_pass`` repairs that on the card,
so the card's front never met the list's key and the common word had no rank.
Given the dictionaries folder, each word is therefore also a one-word line
through that post-pass, over the dictionaries installed there for the language
and with the two lookups ``service_factory`` hands the parser. With no such
dictionary, or for a language whose parser has no post-pass, the key is the
tagger's lemma, as before.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.dictionary.registry import DictionaryRegistry
    from anki_miner.services.morphology import TokenPostPass
    from anki_miner.services.subtitle_parser import SubtitleParserService

Lemmatizer = Callable[[list[str]], list[str]]
#: One line's raw tagger tokens -> the tokens a card front is read from.
_LinePass = Callable[[list[Any]], list[Any]]


class LemmatizeKwarg(TypedDict, total=False):
    """The ``lemmatize=`` keyword an importer call is splatted with (S17).

    Absent when there is no lemmatizer, so such a call keeps its pre-S17 shape
    (the ``LanguageKwarg`` idiom).
    """

    lemmatize: Lemmatizer


def lemmatize_kwarg(lemmatize: Lemmatizer | None) -> LemmatizeKwarg:
    """``{"lemmatize": lemmatize}``, or nothing at all when there is none."""
    return {} if lemmatize is None else {"lemmatize": lemmatize}


def build_frequency_lemmatizer(language: str, dicts_root: Path | None = None) -> Lemmatizer:
    """Return a lemmatizer over *language*'s tagger, resolved on first call.

    A term the tagger splits into more than one token (``don't``) is not a word
    the list can re-rank, so it keeps its own spelling. Built lazily: the tagger
    may be an engine that costs seconds to load, and the import runs off the GUI
    thread.

    *dicts_root* is the dictionaries folder (see the module docstring). It is
    scanned on the first call, not at build time: the catalogue imports its
    dictionary just before its list. Each call opens those dictionaries and
    closes them again, so no index stays open past the chunk it served.
    """

    @functools.cache
    def front_repair() -> _FrontRepair | None:
        return None if dicts_root is None else _FrontRepair.find(language, dicts_root)

    def lemmatize(words: list[str]) -> list[str]:
        from anki_miner.languages.tagger_provider import get_tagger
        from anki_miner.services.morphology import extract_lemma

        tagger = get_tagger(language)
        repair = front_repair()
        lemmas: list[str] = []
        with repair.opened() if repair is not None else nullcontext(list) as line_pass:
            for word in words:
                tokens = line_pass(list(tagger(word)))
                lemmas.append(extract_lemma(tokens[0]) if len(tokens) == 1 else word)
        return lemmas

    return lemmatize


class _FrontRepair:
    """*language*'s parser post-pass over the dictionaries installed for *language*."""

    def __init__(self, post_pass: TokenPostPass, config: AnkiMinerConfig, registry: DictionaryRegistry) -> None:
        self._post_pass = post_pass
        self._config = config
        self._registry = registry

    @classmethod
    def find(cls, language: str, dicts_root: Path) -> _FrontRepair | None:
        """The repair, or None when the parser has no post-pass or no dictionary is stamped for *language*.

        The chain is every schema-current slot under *dicts_root* stamped for
        *language*, in id order: the list is imported before the settings chain
        it will join exists, so the installed slots stand in for it.
        """
        from dataclasses import replace

        from anki_miner.config import AnkiMinerConfig, ChainEntry
        from anki_miner.languages.registry import get_profile
        from anki_miner.services.dictionary.registry import DictionaryRegistry

        config = AnkiMinerConfig(language=language, dicts_root=dicts_root, dictionary_chain=())
        # Every factory builds a SubtitleParserService (service_factory.create_profile_parser casts alike).
        post_pass = cast("SubtitleParserService", get_profile(language).create_parser(config)).token_post_pass
        if post_pass is None:
            return None
        registry = DictionaryRegistry(dicts_root)
        registry.load()
        # unlisted() of an empty chain is every schema-current slot on disk.
        chain = tuple(
            ChainEntry(kind="indexed", dict_id=meta.dict_id)
            for meta in registry.unlisted(config)
            if meta.language == language
        )
        return cls(post_pass, replace(config, dictionary_chain=chain), registry) if chain else None

    @contextmanager
    def opened(self) -> Iterator[_LinePass]:
        """The line pass over freshly opened dictionaries, closed on exit.

        Built like ``service_factory.build_definition_service``; its
        ``offline_terms_exist`` and ``offline_term_rows`` are the probe and the
        form rows ``create_services`` wires into the parser.
        """
        from anki_miner.services.definition_service import DefinitionService

        service = DefinitionService(
            self._config, providers=self._registry.build_provider_chain(self._config), registry=self._registry
        )
        try:
            service.ensure_loaded()
            yield lambda tokens: list(self._post_pass(tokens, service.offline_terms_exist, service.offline_term_rows))
        finally:
            service.close()


def manual_import_lemmatizer(language: str, dicts_root: Path | None = None) -> Lemmatizer | None:
    """The lemmatizer for a hand-added list, or None when the language does not declare one.

    Declared by the ``lemmatised_frequency`` capability: only a profile whose
    tagger lemmatises reliably opts a user's own list into aggregation.
    *dicts_root* as for :func:`build_frequency_lemmatizer`.
    """
    from anki_miner.languages.registry import get_profile

    if "lemmatised_frequency" not in get_profile(language).capabilities:
        return None
    return build_frequency_lemmatizer(language, dicts_root)
