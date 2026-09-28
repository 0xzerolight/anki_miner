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
and with the two lookups ``service_factory`` hands the parser; one move that
rides on the lone word's part-of-speech guess is undone (``_FrontRepair``). With
no such dictionary, or for a language whose parser has no post-pass, the key is
the tagger's lemma, as before.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services.definition_service import DefinitionService
    from anki_miner.services.dictionary.registry import DictionaryRegistry
    from anki_miner.services.morphology import TokenPostPass
    from anki_miner.services.subtitle_parser import SubtitleParserService

Lemmatizer = Callable[[list[str]], list[str]]
#: One list word and its raw tagger tokens -> the key its count is filed under.
_WordKey = Callable[[str, list[Any]], str]


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

        tagger = get_tagger(language)
        repair = front_repair()
        with repair.opened() if repair is not None else nullcontext(_tagger_key) as key:
            return [key(word, list(tagger(word))) for word in words]

    return lemmatize


def _tagger_key(word: str, tokens: list[Any]) -> str:
    """The lone token's lemma; a word split into several tokens keeps its own spelling."""
    from anki_miner.services.morphology import extract_lemma

    return extract_lemma(tokens[0]) if len(tokens) == 1 else word


class _FrontRepair:
    """*language*'s parser post-pass over the dictionaries installed for *language*.

    The post-pass trusts the token's part of speech, and a word tagged alone carries only the
    tagger's guess at one: fr_core_news_sm tags a lone ``gare`` VERB with lemma ``gare``, and the
    ``-e`` repair files it under ``garer``, while the card for ``la gare`` fronts ``gare``. So a word
    the tagger keeps as its own lemma keeps it when the post-pass moves it to a class none of the
    word's own headword rows has (the surface-first rule of ``_spaced/form_of.py``). The move stands
    for a word the dictionary files only as forms or names (fr ``parle`` -> ``parler``, tr ``sever``
    -> ``sevmek``), for a move inside the word's class (el ``ξέρεις`` -> ``ξέρω``: its ``v`` row is an
    inflection gloss), and for a word the tagger lemmatised to another spelling (``viens`` ->
    ``vien`` -> ``venir``, ``est`` -> ``être``).
    """

    def __init__(
        self,
        post_pass: TokenPostPass,
        config: AnkiMinerConfig,
        registry: DictionaryRegistry,
        fold: Callable[[str], str],
    ) -> None:
        self._post_pass = post_pass
        self._config = config
        self._registry = registry
        self._fold = fold

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
        profile = get_profile(language)
        # Every factory builds a SubtitleParserService (service_factory.create_profile_parser casts alike).
        post_pass = cast("SubtitleParserService", profile.create_parser(config)).token_post_pass
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
        if not chain:
            return None
        return cls(post_pass, replace(config, dictionary_chain=chain), registry, profile.dict_keys.fold_term)

    @contextmanager
    def opened(self) -> Iterator[_WordKey]:
        """The word key over freshly opened dictionaries, closed on exit.

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
            yield functools.partial(self._key, service)
        finally:
            service.close()

    def _key(self, service: DefinitionService, word: str, tokens: list[Any]) -> str:
        """*word*'s key: the post-pass front, unless that moves a headword out of its class (class docstring)."""
        from anki_miner.languages._spaced.form_of import WTY_TAG_TO_UPOS, is_lemma_row

        tagged = _tagger_key(word, tokens)  # read first: the post-pass rewrites the tokens in place
        tokens = list(self._post_pass(tokens, service.offline_terms_exist, service.offline_term_rows))
        key = _tagger_key(word, tokens)
        if self._fold(tagged) != self._fold(word) or self._fold(key) == self._fold(word):
            return key
        rows = service.offline_term_rows([word]).get(word, ())
        classes = {WTY_TAG_TO_UPOS.get(tags.split(" ")[0]) for _content, tags in rows if is_lemma_row(tags)}
        classes -= {None, "PROPN"}
        return word if classes and tokens[0].feature.pos1 not in classes else key


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
