"""Integration tests for the definition lookup pipeline."""

from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.providers.jisho_provider import JishoProvider
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)


class _StubOfflineProvider:
    """A minimal in-memory provider used to mimic an indexed offline dictionary."""

    def __init__(self, entries: dict[str, str]):
        self._entries = entries
        self._loaded = False

    @property
    def name(self) -> str:
        return "StubOffline"

    def is_available(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        self._loaded = True
        return True

    def lookup(self, word: str) -> str | None:
        return self._entries.get(word)


@pytest.fixture
def config(tmp_path):
    return AnkiMinerConfig(media_temp_folder=tmp_path / "media")


@pytest.fixture
def offline_provider():
    """Stub offline provider with a handful of canned entries."""
    return _StubOfflineProvider(
        {
            "食べる": "1. to eat<br>2. to live on (e.g. a salary); to live off",
            "学生": "1. student (esp. a university student)",
            "走る": "1. to run<br>2. to travel (movement of vehicles); to drive<br>3. to hurry to",
        }
    )


class TestDefinitionPipeline:
    """End-to-end tests over DefinitionService with offline + Jisho fallback."""

    def test_offline_hit_skips_jisho(self, config, offline_provider):
        """When the offline provider hits, Jisho is never queried."""
        jisho = JishoProvider(delay=0)
        service = DefinitionService(config, providers=[offline_provider, jisho])

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result = service.get_definitions_batch([("食べる", None)])[0]
        assert result is not None
        assert "to eat" in result
        mock_jisho.assert_not_called()

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result2 = service.get_definitions_batch([("学生", None)])[0]
        assert result2 is not None
        assert "student" in result2
        mock_jisho.assert_not_called()

    def test_fallback_to_jisho_when_word_not_in_offline(self, config, offline_provider):
        """When offline misses, the service queries the next provider (Jisho)."""
        jisho = JishoProvider(delay=0)
        service = DefinitionService(config, providers=[offline_provider, jisho])

        # "飲む" is NOT in the stub; Jisho should be queried as fallback.
        with patch.object(JishoProvider, "lookup", return_value="1. to drink") as mock_jisho:
            result = service.get_definitions_batch([("飲む", None)])[0]

        mock_jisho.assert_called_once_with("飲む")
        assert result == "1. to drink"

    def test_batch_mixed_results(self, config, offline_provider):
        """Batch lookup with a mix of offline hits and Jisho fallbacks."""
        jisho = JishoProvider(delay=0)
        service = DefinitionService(config, providers=[offline_provider, jisho])

        # 飲む is missing, so its value comes from the Jisho fallback.
        with patch.object(JishoProvider, "lookup", return_value="1. to drink"):
            results = service.get_definitions_batch([("食べる", None), ("飲む", None), ("走る", None)])

        assert len(results) == 3
        assert results[0] is not None  # 食べる found in offline
        assert "to eat" in results[0]
        assert results[1] == "1. to drink"  # 飲む via Jisho fallback
        assert results[2] is not None  # 走る found in offline
        assert "to run" in results[2]


def _seed_indexed(root: Path, dict_id: str, rows: list[DictRow]) -> IndexedDictProvider:
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, rows)
    write_meta(db, {"schema_version": str(SCHEMA_VERSION), "source_name": dict_id})
    return IndexedDictProvider(dict_id, db, display_name=dict_id)


class TestReadingBoostThroughPipeline:
    """The sentence's contextual reading, threaded as (word, reading) pairs,
    reaches the real IndexedDictProvider and boosts the matching-reading sense to
    the front WITHOUT dropping the other homograph's sense (5.1, full stack)."""

    def test_homograph_reading_boosts_definition(self, config, tmp_path):
        provider = _seed_indexed(
            tmp_path / "dicts",
            "homograph-dict",
            [
                DictRow(term="辛い", reading="からい", content='<li class="gloss-item">spicy</li>', sequence=1),
                DictRow(term="辛い", reading="つらい", content='<li class="gloss-item">painful</li>', sequence=2),
            ],
        )
        service = DefinitionService(config, providers=[provider])
        try:
            spicy = service.get_definitions_batch([("辛い", "からい")])[0]
            painful = service.get_definitions_batch([("辛い", "つらい")])[0]
        finally:
            provider.close()

        assert spicy is not None and painful is not None
        # からい reading leads with spicy first; つらい reading leads with painful.
        assert spicy.index("spicy") < spicy.index("painful")
        assert painful.index("painful") < painful.index("spicy")
        # Boost-not-filter: the non-matching sense still survives in both.
        assert "painful" in spicy
        assert "spicy" in painful
