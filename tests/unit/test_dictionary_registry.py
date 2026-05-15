"""Tests for DictionaryRegistry."""

from pathlib import Path

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
)
from anki_miner.services.providers.jisho_provider import JishoProvider


def _seed_dict(root: Path, dict_id: str, source_name: str):
    folder = root / dict_id
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "index.sqlite"
    create_index(db)
    bulk_insert(db, [DictRow(term="x", reading=None, content="<div>x</div>", sequence=1)])
    write_meta(
        db,
        {
            "schema_version": str(SCHEMA_VERSION),
            "source_name": source_name,
            "format": "yomitan",
            "entry_count": "1",
        },
    )


class TestDictionaryRegistry:
    def test_scan_lists_installed_dicts(self, tmp_path: Path):
        _seed_dict(tmp_path, "daijirin-v1", "大辞林")
        _seed_dict(tmp_path, "jmdict-english", "JMdict (English)")

        registry = DictionaryRegistry(tmp_path)
        ids = {meta.dict_id for meta in registry.list_dicts()}
        assert ids == {"daijirin-v1", "jmdict-english"}

    def test_scan_skips_corrupt_folder_with_warning(self, tmp_path: Path, caplog):
        _seed_dict(tmp_path, "good", "Good")
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "index.sqlite").write_bytes(b"not a sqlite file")

        registry = DictionaryRegistry(tmp_path)
        ids = {meta.dict_id for meta in registry.list_dicts()}
        assert "good" in ids
        assert "bad" not in ids

    def test_build_chain_respects_config_order(self, tmp_path: Path):
        _seed_dict(tmp_path, "daijirin-v1", "大辞林")
        _seed_dict(tmp_path, "jmdict-english", "JMdict")

        config = AnkiMinerConfig()
        from dataclasses import replace

        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="daijirin-v1", enabled=True),
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        chain = registry.build_provider_chain(config)

        assert len(chain) == 3
        assert isinstance(chain[0], IndexedDictProvider)
        assert chain[0].dict_id == "daijirin-v1"
        assert isinstance(chain[1], IndexedDictProvider)
        assert chain[1].dict_id == "jmdict-english"
        assert isinstance(chain[2], JishoProvider)

    def test_build_chain_skips_disabled_entries(self, tmp_path: Path):
        _seed_dict(tmp_path, "jmdict-english", "JMdict")
        config = AnkiMinerConfig()
        from dataclasses import replace

        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        chain = registry.build_provider_chain(config)
        assert len(chain) == 1
        assert isinstance(chain[0], JishoProvider)

    def test_build_chain_drops_missing_dict_with_warning(self, tmp_path: Path, caplog):
        # No dicts on disk; config references one
        config = AnkiMinerConfig()
        from dataclasses import replace

        config = replace(
            config,
            dictionary_chain=(
                ChainEntry(kind="indexed", dict_id="ghost", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            ),
        )

        registry = DictionaryRegistry(tmp_path)
        chain = registry.build_provider_chain(config)
        assert len(chain) == 1
        assert isinstance(chain[0], JishoProvider)

    def test_disk_only_dict_returned_by_list_dicts(self, tmp_path: Path):
        """Dictionaries on disk that aren't in the config should still appear
        in list_dicts so the UI can offer to enable them."""
        _seed_dict(tmp_path, "new-on-disk", "Surprise Dict")

        registry = DictionaryRegistry(tmp_path)
        ids = {meta.dict_id for meta in registry.list_dicts()}
        assert "new-on-disk" in ids
