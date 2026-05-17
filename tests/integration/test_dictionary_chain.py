"""End-to-end test: chain with two indexed dicts + Jisho fallback."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)


def _seed(root: Path, dict_id: str, entries: list[DictRow]) -> None:
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, entries)
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": dict_id,
            "format": "yomitan",
            "entry_count": str(len(entries)),
        },
    )


def test_first_hit_wins_indexed_before_jisho(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "high-priority",
        [DictRow(term="食べる", reading="たべる", content="<div>HIGH PRIORITY</div>", sequence=1)],
    )
    _seed(
        tmp_path,
        "low-priority",
        [DictRow(term="食べる", reading="たべる", content="<div>LOW PRIORITY</div>", sequence=1)],
    )

    config = replace(
        AnkiMinerConfig(),
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="high-priority", enabled=True),
            ChainEntry(kind="indexed", dict_id="low-priority", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        ),
    )

    registry = DictionaryRegistry(tmp_path)
    chain = registry.build_provider_chain(config)
    service = DefinitionService(config, providers=chain)

    with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get") as mock_get:
        # If Jisho is called, the test fails — high-priority should hit first
        mock_get.side_effect = AssertionError("Jisho should not be called when indexed dict hits")
        result = service.get_definition("食べる")
        # IndexedDictProvider wraps stored content in the Yomitan/Lapis envelope
        # (see Task 4). The seeded content must appear inside that envelope,
        # and the low-priority entry must not.
        assert result is not None
        assert "HIGH PRIORITY" in result
        assert "LOW PRIORITY" not in result
        assert 'data-dictionary="high-priority"' in result
        assert 'class="yomitan-glossary"' in result


def test_falls_through_to_jisho_when_no_indexed_hit(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "only-dict",
        [DictRow(term="食べる", reading="たべる", content="<div>eat</div>", sequence=1)],
    )

    config = replace(
        AnkiMinerConfig(),
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="only-dict", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        ),
        jisho_delay=0.0,
    )

    registry = DictionaryRegistry(tmp_path)
    chain = registry.build_provider_chain(config)
    service = DefinitionService(config, providers=chain)

    with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get") as mock_get:
        mock_response = mock_get.return_value
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"senses": [{"english_definitions": ["jisho fallback"]}]}]}
        result = service.get_definition("聞く")  # not in the local dict
        assert result is not None
        assert "jisho fallback" in result
        mock_get.assert_called()


def test_glossary_concatenates_two_offline_dicts(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "dict-a",
        [
            DictRow(
                term="食べる",
                reading="たべる",
                content='<li class="gloss-item">def from A</li>',
                sequence=1,
            )
        ],
    )
    _seed(
        tmp_path,
        "dict-b",
        [
            DictRow(
                term="食べる",
                reading="たべる",
                content='<li class="gloss-item">def from B</li>',
                sequence=1,
            )
        ],
    )

    config = replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path,
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="dict-a", enabled=True),
            ChainEntry(kind="indexed", dict_id="dict-b", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        ),
    )

    registry = DictionaryRegistry(config.dicts_root)
    providers = registry.build_provider_chain(config)
    service = DefinitionService(config, providers)

    # Jisho must not be called when offline dicts hit. Patch the HTTP call to
    # blow up if invoked, so any accidental online lookup fails loudly.
    with patch(
        "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
        side_effect=AssertionError("Jisho should not be called when offline hits exist"),
    ):
        result = service.get_glossary("食べる")

    assert result is not None
    assert result.count('<div class="yomitan-glossary">') == 2
    assert 'data-dictionary="dict-a"' in result
    assert 'data-dictionary="dict-b"' in result
    assert "def from A" in result
    assert "def from B" in result


def test_glossary_falls_back_to_jisho_when_no_offline_hit(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        "dict-empty",
        [
            DictRow(
                term="別の語",
                reading="べつのご",
                content='<li class="gloss-item">other</li>',
                sequence=1,
            )
        ],
    )

    config = replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path,
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="dict-empty", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        ),
        jisho_delay=0,
    )

    class _R:
        status_code = 200

        def json(self):
            return {"data": [{"senses": [{"english_definitions": ["to eat"]}]}]}

    registry = DictionaryRegistry(config.dicts_root)
    providers = registry.build_provider_chain(config)
    service = DefinitionService(config, providers)

    with patch(
        "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
        return_value=_R(),
    ):
        result = service.get_glossary("食べる")

    assert result is not None
    assert 'data-dictionary="Jisho API"' in result
    assert "to eat" in result
