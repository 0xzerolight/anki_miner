"""The Glossary walks the same lookup-miss ladder as the Definition (ID-03).

A front the dictionaries miss but the profile's ladder resolves (id ``bukunya``
-> ``buku``, ja ``探し`` -> ``探す``) got a Definition and a blank Glossary:
only ``get_definitions_batch`` took the ``fallback_context``. The Glossary takes
it too and stacks every offline dictionary's hit for the first candidate any of
them answers, so phase 4 keeps no Glossary retry of its own.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from anki_miner.languages.registry import get_profile
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, DictRow, bulk_insert, create_index, write_meta
from tests.conftest import build_processor


class _Provider:
    """Offline dictionary: exact hits through ``lookup_many``, ladder hits through ``lookup_fallback``."""

    is_online = False

    def __init__(self, name: str, exact: dict[str, str] | None = None, fallback: dict[str, str] | None = None):
        self.name = name
        self._exact = exact or {}
        self._fallback = fallback or {}
        self.fallback_calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def load(self) -> bool:
        return True

    def lookup(self, word: str) -> str | None:
        return self._exact.get(word)

    def lookup_many(self, pairs, **_kwargs):
        return {word: self._exact.get(word) for word, _reading in pairs}

    def lookup_fallback(self, word: str, conditions: int) -> str | None:
        self.fallback_calls.append(word)
        return self._fallback.get(word)


class _Ladder:
    """LookupStrategy answering a fixed candidate list."""

    def __init__(self, *candidates: str):
        self._candidates = [(text, 0) for text in candidates]

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        return list(self._candidates)


def _seed(root: Path, dict_id: str, rows: list[DictRow], keys=None) -> IndexedDictProvider:
    folder = root / dict_id
    folder.mkdir(parents=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, rows)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(rows)),
        },
    )
    provider = IndexedDictProvider(dict_id, db, display_name=dict_id, keys=keys)
    provider.load()
    return provider


def _gloss(text: str) -> str:
    return f'<li class="gloss-item">{text}</li>'


class TestGlossaryBatchLadder:
    def test_unresolved_pair_stacks_every_provider_for_the_first_hitting_candidate(self, test_config):
        first = _Provider("A", fallback={"buku": "<A buku>"})
        second = _Provider("B", fallback={"buku": "<B buku>"})
        third = _Provider("C", fallback={"bu": "<C bu>"})
        service = DefinitionService(test_config, [first, second, third], lookup=_Ladder("bukux", "buku", "bu"))

        glossaries = service.get_glossaries_batch([("bukunya", None)], None, {"bukunya": ("bukunya", None)})

        # A deeper candidate ("bu") never mixes into the first hitting one's hits.
        assert glossaries == ["<A buku><B buku>"]
        assert third.fallback_calls == ["bukux", "buku"]

    def test_no_fallback_context_keeps_the_exact_only_walk(self, test_config):
        provider = _Provider("A", fallback={"buku": "<A buku>"})
        service = DefinitionService(test_config, [provider], lookup=_Ladder("buku"))

        assert service.get_glossaries_batch([("bukunya", None)]) == [None]
        assert provider.fallback_calls == []

    def test_a_pair_any_dictionary_answers_never_walks_the_ladder(self, test_config):
        hit = _Provider("A", exact={"buku": "<A buku>"})
        miss = _Provider("B", fallback={"bu": "<B bu>"})
        service = DefinitionService(test_config, [hit, miss], lookup=_Ladder("bu"))

        glossaries = service.get_glossaries_batch([("buku", None)], None, {"buku": ("buku", None)})

        assert glossaries == ["<A buku>"]
        assert hit.fallback_calls == miss.fallback_calls == []

    def test_the_definition_ladder_stays_first_hit(self, test_config):
        first = _Provider("A", fallback={"buku": "<A buku>"})
        second = _Provider("B", fallback={"buku": "<B buku>"})
        service = DefinitionService(test_config, [first, second], lookup=_Ladder("buku"))

        definitions = service.get_definitions_batch([("bukunya", None)], None, {"bukunya": ("bukunya", None)})

        assert definitions == ["<A buku>"]

    def test_indonesian_clitic_front_gets_both_dictionaries(self, test_config, tmp_path):
        keys = get_profile("id").dict_keys
        wty = _seed(tmp_path, "wty", [DictRow(term="buku", reading=None, content=_gloss("book"))], keys)
        other = _seed(tmp_path, "other", [DictRow(term="buku", reading=None, content=_gloss("volume"))], keys)
        service = DefinitionService(test_config, [wty, other], lookup=get_profile("id").lookup)

        (glossary,) = service.get_glossaries_batch([("bukunya", None)], None, {"bukunya": ("bukunya", None)})

        assert glossary is not None
        assert glossary.index("book") < glossary.index("volume")

    def test_the_ladder_covers_the_japanese_okurigana_alternate(self, test_config, tmp_path):
        """The retired phase-4 retry's case: 探し misses, its okurigana lemma 探す hits."""
        jmdict = _seed(
            tmp_path, "jmdict", [DictRow(term="探す", reading="さがす", content=_gloss("search"), rules="v5s")]
        )
        service = DefinitionService(test_config, [jmdict])

        (glossary,) = service.get_glossaries_batch([("探し", "さがし")], None, {"探し": ("探す", None)})

        assert glossary is not None and "search" in glossary


class TestPhase4GlossaryLadder:
    def _processor(self, config, definition_service):
        glossary_config = replace(config, anki_fields={**config.anki_fields, "glossary": "Glossary"})
        return build_processor(glossary_config, definition_service=definition_service)

    def test_the_glossary_batch_receives_the_ladder_context_once(self, test_config, mock_services):
        service = mock_services["definition_service"]
        service.get_definitions_batch.return_value = ["1. search"]
        service.get_glossaries_batch.return_value = [None]
        word = TokenizedWord(
            surface="探し",
            lemma="探す",
            reading="サガシ",
            sentence="探しに行く",
            start_time=1.0,
            end_time=2.0,
            duration=1.0,
            lemma_reading="さがす",
            expression_reading="さがし",
            pos="名詞",
        )

        self._processor(test_config, service)._phase4_lookup(None, [(word, MediaData())], None)

        service.get_glossaries_batch.assert_called_once()
        assert service.get_glossaries_batch.call_args.args == ([("探し", "さがし")], None, {"探し": ("探す", None)})

    def test_an_indonesian_ladder_front_fills_both_fields(self, test_config, tmp_path):
        config = replace(test_config, language="id")
        keys = get_profile("id").dict_keys
        wty = _seed(tmp_path, "wty", [DictRow(term="buku", reading=None, content=_gloss("book"))], keys)
        service = DefinitionService(config, [wty], lookup=get_profile("id").lookup)
        word = TokenizedWord(
            surface="bukunya",
            lemma="bukunya",
            reading="",
            sentence="Bukunya di meja.",
            start_time=1.0,
            end_time=2.0,
            duration=1.0,
            mined_form_override="bukunya",
        )

        definitions, glossaries, _pitch = self._processor(config, service)._phase4_lookup(
            None, [(word, MediaData())], None
        )

        assert definitions[0] is not None and "book" in definitions[0]
        assert glossaries[0] is not None and "book" in glossaries[0]
