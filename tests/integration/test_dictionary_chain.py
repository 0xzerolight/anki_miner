"""End-to-end test: chain with two indexed dicts."""

from dataclasses import replace
from pathlib import Path

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


def test_first_hit_wins_in_chain_order(tmp_path: Path) -> None:
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
        ),
    )

    registry = DictionaryRegistry(tmp_path)
    registry.load()
    chain = registry.build_provider_chain(config)
    service = DefinitionService(config, providers=chain)

    result = service.get_definitions_batch([("食べる", None)])[0]
    # IndexedDictProvider wraps stored content in the Yomitan/Lapis envelope
    # (see Task 4). The seeded content must appear inside that envelope,
    # and the low-priority entry must not.
    assert result is not None
    assert "HIGH PRIORITY" in result
    assert "LOW PRIORITY" not in result
    assert 'data-dictionary="high-priority"' in result
    assert 'class="yomitan-glossary"' in result


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
        ),
    )

    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    providers = registry.build_provider_chain(config)
    service = DefinitionService(config, providers)

    result = service.get_glossaries_batch([("食べる", None)])[0]

    assert result is not None
    assert result.count('<div class="yomitan-glossary">') == 2
    assert 'data-dictionary="dict-a"' in result
    assert 'data-dictionary="dict-b"' in result
    assert "def from A" in result
    assert "def from B" in result
